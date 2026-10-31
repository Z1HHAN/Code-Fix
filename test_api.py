import os
from openai import OpenAI

api_key = os.getenv("OPENAI_API_KEY")
model_name = os.getenv("OPENAI_MODEL")
base_url = os.getenv("OPENAI_BASE_URL")

if not api_key:
    raise RuntimeError("请先设置 OPENAI_API_KEY")
if not model_name:
    raise RuntimeError("请先设置 OPENAI_MODEL")

client_options = {"api_key": api_key}
if base_url:
    client_options["base_url"] = base_url

client = OpenAI(**client_options)
response = client.chat.completions.create(
    model=model_name,
    messages=[{"role": "user", "content": "把这句话原样返回：闭环OK"}],
)

print(response.choices[0].message.content)
