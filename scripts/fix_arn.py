import re
import sys

arn = "arn:aws:bedrock-agentcore:us-east-1:741448928336:runtime/src_agentcore_entrypoint-cVDQ4N6w4Q"

with open('/opt/cloudops/config.py', 'r') as f:
    content = f.read()

content = re.sub(r'AGENT_ARN = ".*?"', f'AGENT_ARN = "{arn}"', content)

with open('/opt/cloudops/config.py', 'w') as f:
    f.write(content)

print(f"Updated AGENT_ARN to: {arn}")
