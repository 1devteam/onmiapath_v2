> **Note**: This documentation is for **Omnipath V2**, which has undergone significant stabilization and feature enhancements. For previous versions, please refer to older commits.

# Omnipath V2: Stabilized Multi-Agent AI Platform

**Omnipath V2** is a production-ready, multi-agent AI orchestration platform designed for building scalable, observable, and intelligent autonomous systems. This version has been rigorously stabilized under the **Pride Protocol**, ensuring robust authentication, durable persistence, and high-performance operations. It features a refined architecture with Redis Streams for eventing and a comprehensive suite of observability tools.

![Architecture Diagram](https://private-us-east-1.manuscdn.com/sessionFile/k7GU7hUSA7cxeqZ3oAae2R/sandbox/U1KgzKUZTeTt9PlDSbDP4J-images_1769627043304_na1fn_L2hvbWUvdWJ1bnR1L29tbmlwYXRoLXYzL2RvY3MvYXJjaGl0ZWN0dXJl.png?Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wcml2YXRlLXVzLWVhc3QtMS5tYW51c2Nkbi5jb20vc2Vzc2lvbkZpbGUvazdHVTdoVVNBN2N4ZXFaM29BYWUyUi9zYW5kYm94L1UxS2d6S1VaVGVUdDlQbERTYkRQNEotaW1hZ2VzXzE3Njk2MjcwNDMzMDRfbmExZm5fTDJodmJXVXZkV0oxYm5SMUwyOXRibWx3WVhSb0xYWXpMMlJ2WTNNdllYSmphR2wwWldOMGRYSmwucG5nIiwiQ29uZGl0aW9uIjp7IkRhdGVMZXNzVGhhbiI6eyJBV1M6RXBvY2hUaW1lIjoxNzk4NzYxNjAwfX19XX0_&Key-Pair-Id=K2HSFNDJXOU9YS&Signature=KpnaMayI4qrYPD08MhaDmG0AqDIdt6h8gJvWS8VY-CRxu1vCHyigz4tqh31HHKSG6NhnELlTEVabbiY-ZAB4jP3eYyVJUV71AEI2u77xHnCzXxh99QH6D5M4Vip~2htbZ59~WIeKqlAN83gYdhGQDCg3JHf-ZyVYC5VZOuAKT0f6l~Z-0GZRkfqS1RMbNmSBoHxx5HH5YMJnkoOuvxdqkdHlrNO1nSOdpuEb69iKw6VWx71Kk-VHjpDPruuoHDLCJje8IFFz7z0ifhoi--PXfAX~ta7~h-idDtWG6pa2ZigDLyjycIiR0LoBBI-QuyeS2NQQ6GPbm~tM16YDsxRDKA__)

---

## ✨ Key Features in V2

-   **🚀 Event-Driven Architecture**: Powered by **Redis Streams**, the core enables massive scalability and resilience. Agents communicate asynchronously, eliminating bottlenecks and allowing for independent scaling.

-   **💾 Event Sourcing & CQRS**: Agent state is now an immutable log of events, providing a perfect audit trail and enabling time-travel debugging. CQRS separates read and write concerns for optimal performance.

-   **🔭 Deep Observability**: With **OpenTelemetry** and **Prometheus** integrated, every agent action, LLM call, and system metric is traceable and measurable from a unified dashboard in **Grafana**.

-   **🛡️ Reliable Workflows with Sagas**: Complex, multi-agent missions are orchestrated using the **Saga pattern**, ensuring that workflows either complete successfully or are safely compensated, maintaining data consistency across services.

-   **🧩 Standardized Protocols**: Adopts **Model Context Protocol (MCP)** for tool integration, creating a plug-and-play ecosystem for external services and APIs.

-   **🧠 Emotional Intelligence Core**: Retains its unique ability to factor emotion and risk into agent decision-making, providing a nuanced layer of control not found in other platforms.

-   **🔒 Enterprise-Grade Governance**: Built-in RBAC, multi-tenancy, and immutable audit logs provide the security and compliance features required for enterprise deployment.

---

## 🛠️ Technology Stack

| Category          | Technology                                        | Purpose                                    |
| ----------------- | ------------------------------------------------- | ------------------------------------------ |
| **Web Framework**   | FastAPI                                           | High-performance async API                 |
| **Database**        | PostgreSQL 15+                                    | Primary data store, event store            |
| **Messaging**       | Redis Streams                                     | Event bus for inter-agent communication    |
| **Caching**         | Redis                                             | Session data, read model snapshots         |
| **Observability**   | OpenTelemetry, Prometheus, Jaeger                 | Tracing and metrics observability          |
| **Containerization**| Docker, Kubernetes                                | Scalable and resilient deployment          |


---

## 🚀 Quick Start

This project uses Docker Compose to set up a complete local development environment, including all necessary services.

### Prerequisites

-   Docker and Docker Compose
-   Git

### 1. Clone the Repository

```bash
git clone https://github.com/1devteam/onmiapath_v2
cd omnipath_v2
```

### 2. Configure Environment

Copy the example environment file and update it with your credentials, especially for LLM providers.

```bash
cp .env.example .env
```

**Edit `.env`** and add your API keys:

```env
# LLM Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-...
```

### 3. Launch the Stack

Build and run all services using Docker Compose.

```bash
docker-compose up --build -d
```

This will start:
-   `omnipath-backend` on port `8000`
-   `postgres` on port `5432`
-   `redis` on port `6379`

-   `jaeger` on port `16686` (UI)
-   `prometheus` on port `9090`
-   `grafana` on port `3000`

### 4. Access Services

-   **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
-   **Grafana**: [http://localhost:3000](http://localhost:3000) (user: `admin`, pass: `admin`)
-   **Jaeger Tracing**: [http://localhost:16686](http://localhost:16686)

-   **Prometheus**: [http://localhost:9090](http://localhost:9090)

### 5. Run Database Migrations

Once the backend is running, apply the initial database schema.

```bash
docker-compose exec backend alembic upgrade head
```

---

## 🏛️ Project Structure

```
/omnipath_v2
├── backend/
│   ├── api/            # FastAPI routes
│   ├── agents/         # Agent implementations (Commander, Guardian, etc.)
│   ├── core/
│   │   ├── event_bus/  # Redis Streams implementation
│   │   └── event_sourcing/ # Event store logic
│   ├── integrations/   # 3rd-party services (Observability, MCP)
│   ├── models/         # SQLAlchemy data models
│   ├── orchestration/  # Saga orchestrator
│   ├── services/       # Business logic (Auth, RBAC)
│   ├── main.py         # Application entry point
│   └── config/         # Settings and configuration
├── docs/
│   ├── ARCHITECTURE.md # Detailed architecture document
│   └── OMNIPATH_V2_STABILIZATION_REPORT.md # Comprehensive stabilization report
├── monitoring/
│   ├── prometheus.yml  # Prometheus scrape configs
│   └── grafana-datasources.yml # Grafana datasource provisioning
├── .env.example     # Example environment file
├── docker-compose.yml # Docker Compose for the stack
├── Dockerfile          # Application Docker image
└── README.md           # This file
```

---

## 📄 Documentation

-   **[Architecture Deep Dive](docs/ARCHITECTURE.md)**: A detailed explanation of the V2 architecture, components, and design patterns.
-   **[Omnipath V2 Stabilization Report](docs/OMNIPATH_V2_STABILIZATION_REPORT.md)**: A comprehensive report detailing the stabilization efforts and current system state.

---

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## ⚖️ License

This project is proprietary and confidential. Unauthorized use, copying, or distribution is strictly prohibited.

