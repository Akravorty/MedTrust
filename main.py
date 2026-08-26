from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from services.agents.router import router

app = FastAPI(title="MediTrust API", docs_url=None)

# Include Agent Router
app.include_router(router)


@app.get("/", include_in_schema=False)
def root():
    return {"message": "MediTrust API is running"}


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )