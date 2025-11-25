# Newer version of the app.py file
import streamlit as st
from diagnostics import diagnose_pod
from kubernetes import client, config
from kubernetes.config.kube_config import list_kube_config_contexts
from kubernetes.client.rest import ApiException
import json
import re

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
            # Merge parsed JSON into data (status, message, details, etc.)
            data.update(parsed_json)
        except Exception:
            # If JSON parsing fails, include the raw body
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


# Sidebar UI: Cluster -> Namespace -> Pod
st.sidebar.title("Cluster Settings")

contexts_info, active_context_name = get_contexts_and_display()

if not contexts_info:
    st.sidebar.error("No kubeconfig contexts found. Ensure kubeconfig exists.")
    contexts_info = [{"context_name": None, "display_name": "-- No Contexts Found --"}]

# Map display_name -> actual context name
display_to_context = {c["display_name"]: c["context_name"] for c in contexts_info}

# Dropdown display list
display_names = ["-- Select --"] + [c["display_name"] for c in contexts_info]

selected_cluster_display = st.sidebar.selectbox("Cluster", display_names, index=0)

selected_context = None if selected_cluster_display == "-- Select --" else display_to_context.get(selected_cluster_display)

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
namespace_options = ["-- Select --"] + namespace_list
namespace = st.sidebar.selectbox("Namespace", namespace_options, index=0)


# Reset when namespace changes
if "last_namespace" not in st.session_state:
    st.session_state.last_namespace = namespace

if namespace != st.session_state.last_namespace:
    st.session_state.pending_input = ""
    st.session_state.clicked_pod = None
    st.session_state.last_namespace = namespace


# Pod handling (Option 2: always show, default select option)
def get_pods(v1api, ns):
    if not v1api or ns in ("-- Select --", None, ""):
        return []
    try:
        pod_objs = v1api.list_namespaced_pod(ns).items
        return [p.metadata.name for p in pod_objs if p.metadata and p.metadata.name]
    except Exception as e:
        st.sidebar.error(f"Failed to list pods: {e}")
        return []

pod_list = get_pods(v1_for_selected, namespace)
pod_options = ["-- Select --"] + pod_list
selected_pod = st.sidebar.selectbox("Pod", pod_options, index=0)
clicked_pod = None if selected_pod == "-- Select --" else selected_pod


# Chat session handling
if "messages" not in st.session_state:
    st.session_state.messages = []


# Remove invalid placeholder messages
st.session_state.messages = [m for m in st.session_state.messages if m.get("content") not in (None, "None")]


# Render chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"], unsafe_allow_html=True)


# Auto-fill logic for pod name
if "pending_input" not in st.session_state:
    st.session_state.pending_input = ""

if clicked_pod and st.session_state.pending_input != clicked_pod:
    st.session_state.pending_input = clicked_pod

user_input = st.chat_input("Enter Pod name to diagnose...")
prompt = user_input if user_input else st.session_state.pending_input


# Only allow diagnosis when cluster, namespace and pod are selected
can_diagnose = (
    selected_context is not None
    and namespace not in ("-- Select --", "", None)
    and prompt not in ("", None, "-- Select --")
)

# Run diagnostics
if can_diagnose:
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing pod…"):
            # call diagnose_pod with the selected cluster client
            try:
                result = diagnose_pod(v1_for_selected, prompt.strip(), namespace)
            except Exception as e:
                # Unexpected error from diagnose_pod
                err_html = f"<p style='color:red;'>Error: {e}</p>"
                st.error(f"Error: {e}")
                st.session_state.messages.append({"role": "assistant", "content": err_html})
                st.stop()


            # SUMMARY card
            st.markdown(f"""
            <div class="card">
                <h3>Summary</h3>
                <p>{result.get("summary", "")}</p>
            </div>
            """, unsafe_allow_html=True)

            # LIKELY CAUSE card
            st.markdown(f"""
            <div class="card">
                <h3>Likely Cause</h3>
                <p>{result.get("likely_cause", "")}</p>
            </div>
            """, unsafe_allow_html=True)


            # STRUCTURED EVIDENCE
            evidence = result.get("evidence", {})
    
            with st.expander("Evidence Details", expanded=True):

                # CASE 1: evidence is API error string or description text
                if isinstance(evidence, str):

                    if "HTTP response body:" in evidence:
                        st.markdown("### Kubernetes API Error")

                        parsed = parse_k8s_api_error(evidence)

                        for key, value in parsed.items():
                            label = key.replace("_", " ").title()

                            if isinstance(value, dict):
                                st.markdown(f"**{label}:**")
                                st.code(json.dumps(value, indent=2))

                            else:
                                st.markdown(f"**{label}:** {value}")

                    else:
                        st.markdown("### Pod Description Analysis")
                        st.markdown("We detected possible errors inside the Pod's description.")
                        st.code(evidence) 


                # CASE 2: evidence is list (logs/events)
                elif isinstance(evidence, list):
                    st.markdown("**Logs / Events:**")
                    st.code("\n".join([str(x) for x in evidence]))
                    

                # CASE 3: evidence is dict (existing logic)
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


                # CASE 4: fallback
                else:
                    st.write("No evidence available.")



            # LAST LOGS
            if isinstance(evidence, dict) and "last_logs" in evidence:
                with st.expander("Latest Logs"):
                    st.code("\n".join(evidence["last_logs"]))


            # Recommendation card
            st.markdown(f"""
            <div class="card" style="background-color:#1E3A8A; border-color:#3B82F6; color:#F3F4F6;">
                <h3 style="color:#fff;">Recommendation</h3>
                <p style="color:#E0E7FF;">{result.get("recommendation", "")}</p>
            </div>
            """, unsafe_allow_html=True)


            # Build compact HTML for chat history
            compact_html = f"""
            <p><b>Summary:</b> {result.get("summary","")}</p>
            <p><b>Likely Cause:</b> {result.get("likely_cause","")}</p>
            <p><b>Recommendation:</b> {result.get("recommendation","")}</p>
            """


            # Save structured summary into chat history
            st.session_state.messages.append({
                "role": "assistant",
                "content": compact_html
            })

