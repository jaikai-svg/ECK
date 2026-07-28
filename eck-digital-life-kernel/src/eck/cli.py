from __future__ import annotations

import json
from typing import Annotated

import httpx
import typer
import uvicorn
from rich import print

from eck.config import Settings

app = typer.Typer(
    name="eck",
    help="ECK Digital Life Kernel command-line interface.",
    no_args_is_help=True,
)


def _base_url(url: str | None) -> str:
    if url:
        return url.rstrip("/")
    settings = Settings()
    host = "127.0.0.1" if settings.bind_host in {"0.0.0.0", "::"} else settings.bind_host
    return f"http://{host}:{settings.bind_port}"


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Override bind host.")] = None,
    port: Annotated[int | None, typer.Option(help="Override bind port.")] = None,
) -> None:
    """Run the local API and dashboard."""
    settings = Settings()
    uvicorn.run(
        "eck.api.main:app",
        host=host or settings.bind_host,
        port=port or settings.bind_port,
        reload=False,
    )


@app.command()
def status(
    url: Annotated[str | None, typer.Option(help="ECK server URL.")] = None,
) -> None:
    """Show kernel, brain, persistence, and safety health."""
    response = httpx.get(f"{_base_url(url)}/health", timeout=10)
    response.raise_for_status()
    print_json(response.json())


@app.command()
def events(
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 20,
    url: Annotated[str | None, typer.Option(help="ECK server URL.")] = None,
) -> None:
    """List recent durable events."""
    response = httpx.get(f"{_base_url(url)}/v1/events", params={"limit": limit}, timeout=10)
    response.raise_for_status()
    print_json(response.json())


@app.command()
def memory(
    kind: Annotated[
        str,
        typer.Argument(help="experiences, knowledge, reflections, skills, or all"),
    ] = "all",
    limit: Annotated[int, typer.Option(min=1, max=500)] = 20,
    url: Annotated[str | None, typer.Option(help="ECK server URL.")] = None,
) -> None:
    """Inspect evidence-grounded persistent memory records."""
    paths = {
        "experiences": "/v1/experiences",
        "knowledge": "/v1/knowledge",
        "reflections": "/v1/reflections",
        "skills": "/v1/skills",
    }
    if kind != "all" and kind not in paths:
        raise typer.BadParameter(f"kind must be one of: all, {', '.join(paths)}")
    selected = paths if kind == "all" else {kind: paths[kind]}
    result = {}
    for name, path in selected.items():
        response = httpx.get(
            f"{_base_url(url)}{path}",
            params={"limit": limit},
            timeout=10,
        )
        response.raise_for_status()
        result[name] = response.json()["items"]
    print_json(result)


@app.command()
def demo(
    scenario: Annotated[
        str, typer.Argument(help="all, persistence, safe-code, or gridworld")
    ] = "all",
    url: Annotated[str | None, typer.Option(help="ECK server URL.")] = None,
) -> None:
    """Run a deterministic acceptance scenario."""
    allowed = {"all", "persistence", "safe-code", "gridworld"}
    if scenario not in allowed:
        raise typer.BadParameter(f"scenario must be one of: {', '.join(sorted(allowed))}")
    response = httpx.post(
        f"{_base_url(url)}/v1/demos/{scenario}",
        timeout=180,
    )
    response.raise_for_status()
    print_json(response.json())


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
