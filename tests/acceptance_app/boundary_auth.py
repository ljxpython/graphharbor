"""Identity-only Auth fixture shared by the official and GraphHarbor servers."""

from langgraph_sdk import Auth

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None = None):
    if authorization not in {"alice", "bob"}:
        raise Auth.exceptions.HTTPException(401, "Authentication required")
    return {"identity": authorization, "org": "boundary-probe"}


@auth.on
async def deny_other_resources(ctx, value):
    return False


@auth.on.assistants
async def allow_assistants(ctx, value):
    return None


@auth.on.threads
async def scope_threads(ctx, value):
    return {"owner": ctx.user.identity}


@auth.on.threads.create
async def create_thread(ctx, value):
    value.setdefault("metadata", {})["owner"] = ctx.user.identity
    return {"owner": ctx.user.identity}
