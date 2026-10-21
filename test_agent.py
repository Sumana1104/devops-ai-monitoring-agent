from ai_service.app.ai_agent import agent

print("Starting OpsPilot AI Agent...")

result = agent.invoke({
    "incident": "Investigate unhealthy Kubernetes pods"
})

print("\n===== KUBERNETES POD INFORMATION =====")
print(result.get("pod_info"))

print("\n===== GROQ AI ANALYSIS =====")
print(result.get("analysis"))
