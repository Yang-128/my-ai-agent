import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

MODEL = "openai/gpt-oss-120b"

print("My AI Agent is ready. Type 'exit' to quit.")

while True:
    user_input = input("\nYou: ").strip()

    if user_input.lower() in ("exit", "quit"):
        break

    if not user_input:
        continue

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful personal AI assistant."},
                {"role": "user", "content": user_input}
            ],
            max_tokens=1024
        )

        print("\nAgent:", response.choices[0].message.content)

    except Exception as e:
        print("\nError:", e)
