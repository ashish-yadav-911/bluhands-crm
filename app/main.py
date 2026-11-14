from fastapi import FastAPI

from app.routers import agent_jobs, applications, auth, deployments, me, organizations, tenants

app = FastAPI(title="bluhands_crm control plane", version="0.1.0")

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(applications.router)
app.include_router(organizations.router)
app.include_router(tenants.router)
app.include_router(deployments.router)
app.include_router(agent_jobs.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
