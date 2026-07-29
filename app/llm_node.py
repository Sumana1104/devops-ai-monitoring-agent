from groq import Groq
import os

class DevOpsAgent:
    def __init__(self):
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    def ask(self, text: str) -> str:
        response = self.client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are AIBOT, a friendly, calm, gentleman-like DevOps assistant. "
                        "You speak warmly, respectfully, and clearly. "
                        "You explain things simply, like talking to a friend. "
                        "You call the user 'brother' in a friendly way. "
                        "You never sound robotic. "
                        "You keep the tone positive, supportive, and human."
                    )
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        )

        return response.choices[0].message.content
