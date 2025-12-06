import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def ask_groq_llm(summary, cause, recommendation, cluster_context, user_question):

    cluster_context = cluster_context or {}

    kube_context = cluster_context.get("kube_context", "Unknown")
    cluster_name = cluster_context.get("cluster_name", "Unknown")
    namespace = cluster_context.get("namespace", "Unknown")
    pod_name = cluster_context.get("pod_name", "Unknown")

    cluster_block = f"""
    Cluster Context:
    - Kube context: {kube_context}
    - Cluster name: {cluster_name}
    - Namespace: {namespace}
    - Pod: {pod_name}
    """

    system_prompt = f"""
    You are a Kubernetes expert DevOps assistant.

    Here is the diagnostic analysis:

    Summary:
    {summary}

    Likely Cause:
    {cause}

    Recommendation:
    {recommendation}

    {cluster_block}
    Rules:
    - Explain clearly and simply, as if helping a junior DevOps engineer.
    - Use the cluster / namespace / pod context when relevant.
    - Do NOT assume you can run kubectl; only reason from the data provided.
    - Keep answers concise (around 150–200 words) unless the user asks for deep detail.
    - Do not invent new root causes beyond what the evidence supports.
    - Return the answer in markdown format.
    """

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=250,
        temperature=0.3,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question},
        ],
    )

    return response.choices[0].message.content.strip()
