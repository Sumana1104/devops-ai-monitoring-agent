# DevOps AI Monitoring Agent (FastAPI + Prometheus + OpenTelemetry + Kubernetes)

A production-ready cloud-native monitoring microservice built using FastAPI, instrumented with Prometheus metrics, OpenTelemetry traces, and deployed on Kubernetes with full observability and Slack alerting.

This project demonstrates real-world DevOps engineering skills including microservice development, observability, containerization, Kubernetes deployments, health probes, autoscaling, and alerting.

---

## 🚀 Features

- FastAPI microservice with clean REST endpoints
- Prometheus metrics exposed at `/metrics`
- OpenTelemetry tracing with context propagation
- Structured JSON logging
- Slack alerting for failures
- Kubernetes Deployment with:
  - Liveness probe
  - Readiness probe
  - Resource limits
  - Rolling updates
- Dockerized application
- Works with Prometheus, Grafana, Loki, Tempo, OTEL Collector

---

## 🧠 Architecture Overview

- FastAPI service handles requests and generates metrics/traces
- Prometheus scrapes metrics
- Grafana visualizes dashboards
- OpenTelemetry Collector exports traces/logs
- Kubernetes Deployment manages pods, probes, scaling
- Slack Webhook sends alerts
- Docker packages the service
- CI/CD pipeline automates deployment

---
                           ┌──────────────────────────┐
                           │        Client / User     │
                           └──────────────┬───────────┘
                                          │  HTTP Requests
                                          ▼
                           ┌──────────────────────────┐
                           │   FastAPI Monitoring App │
                           │ (AI Agent + httpx calls) │
                           └──────────────┬───────────┘
                                          │  Generates metrics/traces/logs
                                          ▼
        ┌─────────────────────────┬─────────────────────────┬─────────────────────────┐
        │                         │                         │                         │
        ▼                         ▼                         ▼                         ▼
┌──────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐
│   Prometheus Exporter    │  │ OpenTelemetry Collector  │  │      CI/CD Pipeline      │
│ (Scrapes /metrics)       │  │ (Traces + Logs pipeline) │  │ (GitHub → Build → Deploy)│
└──────────────┬───────────┘  └──────────────┬───────────┘  └──────────────┬───────────┘
               │ Metrics data                 │ Traces/Logs                  │ Build Artifact
               ▼                               │                             ▼
┌──────────────────────────┐                   │               ┌──────────────────────────┐
│        Prometheus        │                   │               │     Docker Container      │
│ (Stores metrics)         │                   │               │ (Image pushed to registry)│
└──────────────┬───────────┘                   │               └──────────────┬───────────┘
               │ Visualization queries         │                             │ Deployment
               ▼                               ▼                             ▼
┌──────────────────────────┐        ┌──────────────────────────┐   ┌──────────────────────────┐
│         Grafana          │        │         Tempo (Traces)   │   │     Kubernetes Cluster   │
│ (Dashboards & Alerts)    │        └──────────────────────────┘   │ (Deployment + Service +  │
└──────────────┬───────────┘        ┌──────────────────────────┐   │  Probes + Autoscaling)   │
               │ Alert triggers     │          Loki (Logs)     │   └──────────────────────────┘
               ▼                    └──────────────────────────┘
┌──────────────────────────┐
│          Slack           │
│ (Receives alert messages)│
└──────────────────────────┘



## 📂 Project Structure

titan/
│── app/
│   ├── main.py
│   ├── routes.py
│   ├── metrics.py
│   ├── tracing.py
│   ├── slack_alerts.py
│   └── utils/
│── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── configmap.yaml
│   └── ingress.yaml
│── Dockerfile
│── requirements.txt
│── README.md


---

## ⚙️ Kubernetes Deployment

### Liveness Probe

livenessProbe:
httpGet:
path: /health
port: 8000
initialDelaySeconds: 10
periodSeconds: 5

### Readiness Probe

readinessProbe:
httpGet:
path: /ready
port: 8000
initialDelaySeconds: 5
periodSeconds: 5

resources:
requests:
memory: "256Mi"
cpu: "100m"
limits:
memory: "512Mi"
cpu: "300m"

---

## 📊 Observability

### Prometheus Metrics
- Request count
- Error count
- Latency histogram
- Custom business metrics

### OpenTelemetry Traces
- End-to-end request tracing
- Context propagation
- Exported to Tempo

### Logs
- Structured JSON logs
- Exported to Loki

---

## 🔔 Slack Alerts

Triggered when:
- Error rate > threshold
- Latency > threshold
- Service unavailable
- OOMKilled detected
- CrashLoopBackOff detected

---

## 🐳 Docker Build
docker build -t titan-ai-agent .
docker run -p 8000:8000 titan-ai-agent

---

## ☸️ Deploy to Kubernetes


kubectl apply -f k8s/
kubectl rollout status deployment/titan

Rollback:

---

## 🧪 Health Endpoints

- `/health` → Liveness
- `/ready` → Readiness
- `/metrics` → Prometheus

---

## 👤 Author

Sumana — DevOps Engineer  
Buford, GA  
Building AI-powered monitoring systems and cloud-native microservices.
