import streamlit as st
from diagnostics import diagnose_pod
from kubernetes import client, config
from kubernetes.config.kube_config import list_kube_config_contexts
from llm_groq import ask_groq_llm
import json
import re

if "ai_chat" not in st.session_state:
    st.session_state.ai_chat = []


# Prevent re-diagnosis on AI questions
if "diagnosis_result" not in st.session_state:
    st.session_state.diagnosis_result = None

if "last_diagnosed_pod" not in st.session_state:
    st.session_state.last_diagnosed_pod = None


# Helper: Parse Kubernetes APIException into structured format
def parse_k8s_api_error(error_string):

    data = {}

    # Extract status code and reason e.g. "(404) Reason: NotFound"
    m = re.search(r"\((\d+)\)\s+Reason:\s+([A-Za-z]+)", error_string)
    if m:
        data["status_code"] = m.group(1)
        data["reason"] = m.group(2)

    # Extract JSON from "HTTP response body:" if present
    if "HTTP response body:" in error_string:
        try:
            json_part = error_string.split("HTTP response body:")[1].strip()
            parsed_json = json.loads(json_part)
            data.update(parsed_json)
        except Exception:
            data["full_body"] = json_part if 'json_part' in locals() else error_string

    return data


# Helper: Create CoreV1Api object for a specific kubeconfig context
def make_v1_for_context(context_name):
    if not context_name:
        return None
    try:
        api_client = config.new_client_from_config(context=context_name)
        return client.CoreV1Api(api_client)
    except Exception as e:
        st.sidebar.error(f"Failed to load context '{context_name}': {e}")
        return None


# Load kubeconfig contexts + pretty display names
def get_contexts_and_display():
    try:
        contexts, active_context = list_kube_config_contexts()
        ctx_items = []
        for ctx in contexts:
            name = ctx.get("name")
            display = name.split("_")[-1] if "_" in name else name
            ctx_items.append({"context_name": name, "display_name": display})
        active_name = active_context.get("name") if active_context else None
        return ctx_items, active_name
    except Exception:
        return [], None


# Dark theme CSS
DARK_THEME = """
<style>
body { background-color: #111827; }
[data-testid="stAppViewContainer"] { background-color: #111827; }
h1, h2, h3, p, label { color: #E5E7EB !important; }
.card {
    background-color: #1F2937;
    border: 1px solid #374151;
    padding: 20px;
    border-radius: 10px;
    margin-bottom: 20px;
}
.small-muted { color: #9CA3AF; font-size: 13px; }
</style>
"""
st.markdown(DARK_THEME, unsafe_allow_html=True)


# Page header
st.markdown("""
<div style="text-align: center; padding-top: 10px; padding-bottom: 18px;">
    <h1 style='font-size: 36px; font-weight: 700; margin: 0;'>Kubernetes Pod Diagnostics</h1>
    <p class="small-muted" style='margin-top:6px;'>Analyze and troubleshoot pod failures using Kubernetes insights.</p>
</div>
""", unsafe_allow_html=True)


# Sidebar Width and labels size
st.markdown("""
<style>
/* Sidebar width */
[data-testid="stSidebar"] {
    width: 320px !important;
}

[data-testid="stSidebar"] .stSelectbox label > div,
[data-testid="stSidebar"] .stSelectbox label > span {
    font-size: 18px !important;
    font-weight: 600 !important;
}

/* Add space below the sidebar title container */
[data-testid="stSidebar"] h1, 
[data-testid="stSidebar"] h2, 
[data-testid="stSidebar"] h3 {
    margin-bottom: 18px !important;  /* adjust: 14, 18, 24 */
}
</style>
""", unsafe_allow_html=True)


# Sidebar UI: Cluster -> Namespace -> Pod
st.sidebar.title("⚙️ Cluster Settings")

contexts_info, active_context_name = get_contexts_and_display()

if not contexts_info:
    st.sidebar.error("No kubeconfig contexts found. Ensure kubeconfig exists.")
    contexts_info = [{"context_name": None, "display_name": "-- No Contexts Found --"}]

# Map display_name -> actual context name
display_to_context = {c["display_name"]: c["context_name"] for c in contexts_info}

# Dropdown display list
display_names = ["-- Select a Cluster --"] + [c["display_name"] for c in contexts_info]

selected_cluster_display = st.sidebar.selectbox("☸️ Cluster", display_names, index=0)
st.sidebar.caption("Choose which Kubernetes cluster to connect to")

selected_context = None if selected_cluster_display == "-- Select a Cluster --" else display_to_context.get(selected_cluster_display)

# Create v1 client for the selected context
v1_for_selected = make_v1_for_context(selected_context) if selected_context else None

# Reset state when cluster changes
if "last_cluster" not in st.session_state:
    st.session_state.last_cluster = selected_context

if selected_context != st.session_state.last_cluster:
    st.session_state.pending_input = ""
    st.session_state.messages = []
    st.session_state.last_namespace = None
    st.session_state.last_cluster = selected_context
    st.rerun()


# Namespace handling
def get_namespaces(v1api):
    if not v1api:
        return []
    try:
        pods = v1api.list_pod_for_all_namespaces().items
        return sorted({p.metadata.namespace for p in pods if p.metadata and p.metadata.namespace})
    except Exception as e:
        st.sidebar.error(f"Failed to list namespaces: {e}")
        return []

namespace_list = get_namespaces(v1_for_selected)
namespace_options = ["-- Select a Namespace --"] + namespace_list
namespace = st.sidebar.selectbox("📂 Namespace", namespace_options, index=0)
st.sidebar.caption("Select the namespace where the workload exists")


# Reset when namespace changes
if "last_namespace" not in st.session_state:
    st.session_state.last_namespace = namespace

if namespace != st.session_state.last_namespace:
    st.session_state.pending_input = ""
    st.session_state.clicked_pod = None
    st.session_state.last_namespace = namespace


# Pod handling
def get_pods(v1api, ns):
    if not v1api or ns in ("-- Select a Namespace --", None, ""):
        return []
    try:
        pod_objs = v1api.list_namespaced_pod(ns).items
        return [p.metadata.name for p in pod_objs if p.metadata and p.metadata.name]
    except Exception as e:
        st.sidebar.error(f"Failed to list pods: {e}")
        return []

pod_list = get_pods(v1_for_selected, namespace)
pod_options = ["-- Select a Pod --"] + pod_list
selected_pod = st.sidebar.selectbox("📦 Pod", pod_options, index=0)
st.sidebar.caption("Pick the pod you want to diagnose")
clicked_pod = None if selected_pod == "-- Select a Pod --" else selected_pod


# Auto-fill logic for pod name
if "pending_input" not in st.session_state:
    st.session_state.pending_input = ""

if clicked_pod and st.session_state.pending_input != clicked_pod:
    st.session_state.pending_input = clicked_pod

prompt = st.session_state.pending_input.strip()


# Only allow diagnosis when cluster, namespace and pod are selected
can_diagnose = (
    selected_context is not None
    and namespace not in ("-- Select a Namespace --", "", None)
    and prompt not in ("", None, "-- Select a Pod --")
)

if prompt and prompt != st.session_state.last_diagnosed_pod:
    st.session_state.diagnosis_result = None
    st.session_state.ai_chat = []


# Run diagnostics
if can_diagnose and st.session_state.last_diagnosed_pod != prompt and st.session_state.diagnosis_result is None:

    st.session_state.last_diagnosed_pod = prompt

    try:
        result = diagnose_pod(v1_for_selected, prompt.strip(), namespace)
        st.session_state.diagnosis_result = result
    except Exception as e:
        err_html = f"<p style='color:red;'>Error: {e}</p>"
        st.error(f"Error: {e}")
        st.session_state.messages.append({"role": "assistant", "content": err_html})
        st.stop()


# Render UI ONLY IF we already have a diagnosis
if st.session_state.diagnosis_result is not None:
    result = st.session_state.diagnosis_result

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):

        with st.spinner("Analyzing pod…"):

            st.markdown(f"""
            <div class="card">
                <h3>Summary</h3>
                <p>{result.get("summary", "")}</p>
            </div>
            """, unsafe_allow_html=True)

            st.markdown(f"""
            <div class="card">
                <h3>Likely Cause</h3>
                <p>{result.get("likely_cause", "")}</p>
            </div>
            """, unsafe_allow_html=True)


            # STRUCTURED EVIDENCE
            evidence = result.get("evidence", {})

            with st.expander("Evidence Details", expanded=True):

                if isinstance(evidence, str):
                    if "HTTP response body:" in evidence:
                        st.markdown("### Kubernetes API Error")
                        parsed = parse_k8s_api_error(evidence)

                        excluded_keys = {"kind", "apiVersion", "metadata", "code"}

                        for key, value in parsed.items():
                            if key in excluded_keys:
                                continue
                            label = key.replace("_", " ").title()

                            if isinstance(value, dict):
                                if value:
                                    st.markdown(f"**{label}:**")
                                    st.code(json.dumps(value, indent=2))
                            else:
                                st.markdown(f"**{label}:** {value}")
                    else:
                        st.markdown("### Pod Description Analysis")
                        st.code(evidence)

                elif isinstance(evidence, list):
                    st.markdown("**Logs / Events:**")
                    st.code("\n".join([str(x) for x in evidence]))

                elif isinstance(evidence, dict):
                    for key, value in evidence.items():
                        if key == "last_logs":
                            continue
                        label = key.replace("_", " ").title()

                        if isinstance(value, list):
                            st.markdown(f"**{label}:**")
                            for item in value:
                                st.markdown(f"- {item}")
                        else:
                            st.markdown(f"**{label}:** {value}")
                else:
                    st.write("No evidence available.")


            if isinstance(evidence, dict) and "last_logs" in evidence:
                with st.expander("Latest Logs"):
                    st.code("\n".join(evidence["last_logs"]))

            st.markdown(f"""
            <div class="card" style="background-color:#1E3A8A; border-color:#3B82F6; color:#F3F4F6;">
                <h3 style="color:#fff;">Recommendation</h3>
                <p style="color:#E0E7FF;">{result.get("recommendation", "")}</p>
            </div>
            """, unsafe_allow_html=True)


    # LLM integration code
    for chat in st.session_state.ai_chat:
        with st.chat_message("user"):
            st.markdown(chat["question"])
        with st.chat_message("assistant"):
            st.markdown(chat["answer"])

    ai_user_message = st.chat_input("Ask AI anything about this pod...")

    if ai_user_message:
        with st.chat_message("user"):
            st.markdown(ai_user_message)

        with st.spinner("AI is thinking..."):
            ai_reply = ask_groq_llm(
                summary=st.session_state.diagnosis_result.get("summary"),
                cause=st.session_state.diagnosis_result.get("likely_cause"),
                recommendation=st.session_state.diagnosis_result.get("recommendation"),
                user_question=ai_user_message
            )

        with st.chat_message("assistant"):
            st.markdown(ai_reply)

        st.session_state.ai_chat.append({
            "question": ai_user_message,
            "answer": ai_reply
        })

