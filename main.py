from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from auth import router as auth_router
from Stuff.requests import router as requests_router
from agent.requests import router as agent_requests_router
from Stuff.performance_logs import router as performance_logs_router


from Stuff.staff_departments import router as staff_departments_router
from Stuff.staff_agents import router as staff_agents_router
from Stuff.staff_issue_categories import router as staff_issue_categories_router
from Stuff.staff_assignment import router as staff_assignment_router
from Stuff.staff_zones import router as staff_zones_router
from agent.heatmap import router as heatmap_router
from agent.map_live import router as map_live_router

app = FastAPI(title="CST Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(heatmap_router)
app.include_router(map_live_router)
app.include_router(auth_router)
app.include_router(requests_router)
app.include_router(agent_requests_router)
app.include_router(performance_logs_router)

app.include_router(staff_departments_router)
app.include_router(staff_agents_router)
app.include_router(staff_issue_categories_router)
app.include_router(staff_assignment_router)
app.include_router(staff_zones_router)

@app.get("/")
def root():
    return {"status": "ok"}
