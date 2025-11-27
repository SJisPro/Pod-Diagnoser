import os
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def ask_groq_llm(summary, cause, recommendation, user_question):
    system_prompt = f"""
You are a Kubernetes expert DevOps assistant.

Here is the diagnostic analysis:

Summary:
{summary}

Likely Cause:
{cause}

Recommendation:
{recommendation}

Rules:
- Explain clearly and simply.
- ONLY base answers on the diagnostic data.
- Do NOT exceed 200 words.
- Do not invent new causes or solutions.
"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=250,
        temperature=0.3,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ]
    )

    return response.choices[0].message.content.strip()
