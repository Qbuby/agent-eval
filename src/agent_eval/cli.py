from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="agent-eval", help="Agent automated testing and optimization loop system")
dataset_app = typer.Typer(name="dataset", help="Dataset management (LangSmith-backed)")
app.add_typer(dataset_app)
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_provider():
    from agent_eval.config import settings
    from agent_eval.data.langsmith_provider import LangSmithDatasetProvider

    kwargs: dict[str, Any] = {}
    if settings.langsmith.api_key:
        kwargs["api_key"] = settings.langsmith.api_key
    if settings.langsmith.api_url:
        kwargs["api_url"] = settings.langsmith.api_url
    return LangSmithDatasetProvider(**kwargs)


def _get_manager():
    from agent_eval.data.dataset_manager import DatasetManager
    return DatasetManager(provider=_get_provider())


def _get_extractor():
    from agent_eval.config import settings
    from agent_eval.data.trace_extractor import TraceExtractor

    kwargs: dict[str, Any] = {}
    if settings.langsmith.api_key:
        kwargs["api_key"] = settings.langsmith.api_key
    if settings.langsmith.api_url:
        kwargs["api_url"] = settings.langsmith.api_url
    return TraceExtractor(**kwargs)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# dataset create
# ---------------------------------------------------------------------------

@dataset_app.command("create")
def dataset_create(
    name: str = typer.Argument(..., help="Dataset name"),
    description: str = typer.Option("", "--desc", "-d", help="Dataset description"),
):
    """Create a new dataset in LangSmith."""
    mgr = _get_manager()

    async def _run():
        ds_id = await mgr.create_dataset(name, description)
        console.print(f"[green]Created dataset '{name}' (id={ds_id})[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset list
# ---------------------------------------------------------------------------

@dataset_app.command("list")
def dataset_list(
    name_contains: str | None = typer.Option(None, "--filter", "-f", help="Filter by name"),
):
    """List all datasets."""
    mgr = _get_manager()

    async def _run():
        datasets = await mgr.list_datasets(name_contains)
        if not datasets:
            console.print("[yellow]No datasets found.[/yellow]")
            return

        table = Table(title="Datasets")
        table.add_column("Name", style="cyan")
        table.add_column("Examples", justify="right")
        table.add_column("Description")
        table.add_column("Created", style="dim")

        for ds in datasets:
            table.add_row(
                ds.name,
                str(ds.example_count),
                ds.description[:60] if ds.description else "",
                ds.created_at.strftime("%Y-%m-%d %H:%M") if ds.created_at else "",
            )
        console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset delete
# ---------------------------------------------------------------------------

@dataset_app.command("delete")
def dataset_delete(
    name: str = typer.Argument(..., help="Dataset name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a dataset."""
    if not yes:
        confirm = typer.confirm(f"Delete dataset '{name}'?")
        if not confirm:
            raise typer.Abort()

    mgr = _get_manager()

    async def _run():
        await mgr.delete_dataset(name)
        console.print(f"[green]Deleted dataset '{name}'[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset show
# ---------------------------------------------------------------------------

@dataset_app.command("show")
def dataset_show(
    name: str = typer.Argument(..., help="Dataset name"),
    split: str | None = typer.Option(None, "--split", "-s", help="Filter by split"),
    tag: list[str] | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    as_of: str | None = typer.Option(None, "--as-of", help="Version snapshot (ISO datetime)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max cases to display"),
):
    """Show test cases in a dataset."""
    mgr = _get_manager()

    async def _run():
        cases = await mgr.load_cases(
            name,
            as_of=_parse_datetime(as_of),
            splits=[split] if split else None,
            tags=tag,
            limit=limit,
        )
        if not cases:
            console.print("[yellow]No test cases found.[/yellow]")
            return

        table = Table(title=f"Dataset '{name}' — {len(cases)} case(s)")
        table.add_column("#", justify="right", style="dim")
        table.add_column("ID", style="dim", max_width=12)
        table.add_column("Name", style="cyan")
        table.add_column("Source")
        table.add_column("Tags")
        table.add_column("Input Preview", max_width=40)

        for i, case in enumerate(cases, 1):
            input_preview = ""
            if case.input_messages:
                last_msg = case.input_messages[-1]
                input_preview = str(last_msg.get("content", ""))[:40]

            table.add_row(
                str(i),
                case.id[:12] if case.id else "",
                case.name or "",
                case.source,
                ", ".join(case.tags) if case.tags else "",
                input_preview,
            )
        console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset add-case
# ---------------------------------------------------------------------------

@dataset_app.command("add-case")
def dataset_add_case(
    dataset: str = typer.Argument(..., help="Dataset name"),
    from_file: str = typer.Option(..., "--from-file", "-f", help="JSON file with test case(s)"),
    split: str | None = typer.Option(None, "--split", "-s", help="Split to assign"),
):
    """Add test case(s) from a JSON file."""
    from agent_eval.data.schemas import validate_and_parse

    mgr = _get_manager()
    data = json.loads(Path(from_file).read_text(encoding="utf-8"))
    result = validate_and_parse(data)

    if result.errors:
        for err in result.errors:
            console.print(f"[red]{err}[/red]")
        if not result.cases:
            raise typer.Exit(1)
        console.print(f"[yellow]{len(result.errors)} error(s), proceeding with {len(result.cases)} valid case(s)[/yellow]")

    cases = result.cases

    async def _run():
        if len(cases) <= 5:
            for case in cases:
                ex_id = await mgr.add_case(dataset, case, split=split)
                console.print(f"  Added: {case.name or case.id} -> {ex_id}")
        else:
            await mgr.add_cases_batch(dataset, cases, split=split)
        console.print(f"[green]Added {len(cases)} case(s) to '{dataset}'[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset import-file (deprecated, hidden)
# ---------------------------------------------------------------------------

@dataset_app.command("import-file", hidden=True)
def dataset_import_file(
    dataset: str = typer.Argument(..., help="Dataset name"),
    from_file: str = typer.Option(..., "--from-file", "-f", help="JSON file with test cases"),
    split: str | None = typer.Option(None, "--split", "-s", help="Split to assign"),
):
    """Batch import test cases from a JSON file. (Deprecated: use add-case instead)"""
    console.print("[yellow]import-file is deprecated, use add-case instead[/yellow]")
    dataset_add_case(dataset=dataset, from_file=from_file, split=split)


# ---------------------------------------------------------------------------
# dataset import-traces
# ---------------------------------------------------------------------------

@dataset_app.command("import-traces")
def dataset_import_traces(
    dataset: str = typer.Argument(..., help="Target dataset name"),
    project: str = typer.Option(..., "--project", "-p", help="LangSmith project name"),
    start: str | None = typer.Option(None, "--start", help="Start time (ISO)"),
    end: str | None = typer.Option(None, "--end", help="End time (ISO)"),
    status: str = typer.Option("success", "--status", help="Run status filter"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max runs to list"),
    tag: list[str] | None = typer.Option(None, "--tag", "-t", help="Filter runs by tag"),
    split: str | None = typer.Option(None, "--split", "-s", help="Split to assign"),
    include_output: bool = typer.Option(False, "--include-output", help="Use run output as expected output"),
    auto: bool = typer.Option(False, "--auto", help="Import all matching runs without interactive selection"),
):
    """Import test cases from LangSmith production traces."""
    extractor = _get_extractor()
    mgr = _get_manager()

    async def _run():
        runs = await extractor.list_runs(
            project,
            start_time=_parse_datetime(start),
            end_time=_parse_datetime(end),
            status=status if status != "all" else None,
            tags=tag,
            limit=limit,
        )

        if not runs:
            console.print("[yellow]No matching runs found.[/yellow]")
            return

        table = Table(title=f"Runs from project '{project}'")
        table.add_column("#", justify="right", style="dim")
        table.add_column("ID", style="dim", max_width=12)
        table.add_column("Name", style="cyan")
        table.add_column("Status")
        table.add_column("Latency", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Input Preview", max_width=40)

        for i, r in enumerate(runs, 1):
            table.add_row(
                str(i),
                r.id[:12],
                r.name,
                r.status,
                f"{r.latency_s:.1f}s" if r.latency_s else "-",
                str(r.total_tokens) if r.total_tokens else "-",
                r.input_preview[:40],
            )
        console.print(table)

        if auto:
            selected_ids = [r.id for r in runs]
        else:
            selection = typer.prompt(
                "Enter run numbers to import (comma-separated, or 'all')"
            )
            if selection.strip().lower() == "all":
                selected_ids = [r.id for r in runs]
            else:
                indices = [int(x.strip()) - 1 for x in selection.split(",")]
                selected_ids = [runs[i].id for i in indices if 0 <= i < len(runs)]

        if not selected_ids:
            console.print("[yellow]No runs selected.[/yellow]")
            return

        console.print(f"Extracting {len(selected_ids)} run(s)...")
        cases = await extractor.extract_test_cases(
            selected_ids, include_output_as_expected=include_output
        )

        ids = await mgr.add_cases_batch(
            dataset, cases, split=split, source_run_ids=selected_ids
        )
        console.print(
            f"[green]Imported {len(cases)} case(s) from traces to '{dataset}'[/green]"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset update-case
# ---------------------------------------------------------------------------

@dataset_app.command("update-case")
def dataset_update_case(
    example_id: str = typer.Argument(..., help="Example ID to update"),
    from_file: str = typer.Option(..., "--from-file", "-f", help="JSON file with updated test case"),
):
    """Update a test case by its example ID."""
    from agent_eval.models.test_case import TestCase

    mgr = _get_manager()
    data = json.loads(Path(from_file).read_text(encoding="utf-8"))
    case = TestCase(dataset_version="", **data)

    async def _run():
        await mgr.update_case(example_id, case)
        console.print(f"[green]Updated example {example_id}[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset remove-case
# ---------------------------------------------------------------------------

@dataset_app.command("remove-case")
def dataset_remove_case(
    example_id: str = typer.Argument(..., help="Example ID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a test case by its example ID."""
    if not yes:
        confirm = typer.confirm(f"Delete example '{example_id}'?")
        if not confirm:
            raise typer.Abort()

    mgr = _get_manager()

    async def _run():
        await mgr.delete_case(example_id)
        console.print(f"[green]Deleted example {example_id}[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset versions
# ---------------------------------------------------------------------------

@dataset_app.command("versions")
def dataset_versions(
    name: str = typer.Argument(..., help="Dataset name"),
):
    """Show version history of a dataset."""
    mgr = _get_manager()

    async def _run():
        versions = await mgr.list_versions(name)
        if not versions:
            console.print("[yellow]No versions found.[/yellow]")
            return

        table = Table(title=f"Versions of '{name}'")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Version ID")
        table.add_column("Created At", style="dim")

        for i, v in enumerate(versions, 1):
            table.add_row(
                str(i),
                v.version_id,
                v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "",
            )
        console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset export
# ---------------------------------------------------------------------------

@dataset_app.command("export")
def dataset_export(
    name: str = typer.Argument(..., help="Dataset name"),
    output: str = typer.Option("./export.json", "--output", "-o", help="Output file path"),
    split: str | None = typer.Option(None, "--split", "-s", help="Filter by split"),
    tag: list[str] | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    as_of: str | None = typer.Option(None, "--as-of", help="Version snapshot (ISO datetime)"),
    fmt: str = typer.Option("json", "--format", "-f", help="Output format: json or jsonl"),
):
    """Export test cases to a local file."""
    mgr = _get_manager()

    async def _run():
        data = await mgr.export_cases(
            name,
            as_of=_parse_datetime(as_of),
            splits=[split] if split else None,
            tags=tag,
        )
        if not data:
            console.print("[yellow]No test cases to export.[/yellow]")
            return

        out_path = Path(output)
        if fmt == "jsonl":
            lines = [json.dumps(d, ensure_ascii=False) for d in data]
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        console.print(f"[green]Exported {len(data)} case(s) to {out_path}[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset stats
# ---------------------------------------------------------------------------

@dataset_app.command("stats")
def dataset_stats(
    name: str = typer.Argument(..., help="Dataset name"),
    split: str | None = typer.Option(None, "--split", "-s", help="Filter by split"),
    tag: list[str] | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
):
    """Show dataset statistics."""
    mgr = _get_manager()

    async def _run():
        stats = await mgr.get_stats(
            name, splits=[split] if split else None, tags=tag
        )

        table = Table(title=f"Stats for '{name}'")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total cases", str(stats.total_cases))
        table.add_row("Has expected output", f"{stats.has_expected_output} ({_pct(stats.has_expected_output, stats.total_cases)})")
        table.add_row("Has criteria", f"{stats.has_criteria} ({_pct(stats.has_criteria, stats.total_cases)})")
        table.add_row("Has tool calls", f"{stats.has_tool_calls} ({_pct(stats.has_tool_calls, stats.total_cases)})")
        table.add_row("Avg messages/case", f"{stats.avg_messages_per_case:.1f}")

        if stats.by_source:
            table.add_section()
            for src, cnt in stats.by_source.items():
                table.add_row(f"  source: {src}", str(cnt))

        if stats.by_tag:
            table.add_section()
            for tg, cnt in list(stats.by_tag.items())[:10]:
                table.add_row(f"  tag: {tg}", str(cnt))

        console.print(table)

    asyncio.run(_run())


def _pct(part: int, total: int) -> str:
    return f"{part / total:.0%}" if total else "0%"


# ---------------------------------------------------------------------------
# dataset pull
# ---------------------------------------------------------------------------

@dataset_app.command("pull")
def dataset_pull(
    source: str = typer.Argument(..., help="Source LangSmith dataset name to pull from"),
    target: str | None = typer.Option(
        None, "--target", "-t",
        help="Target dataset to save into (if omitted, only previews the data)",
    ),
    split: str | None = typer.Option(None, "--split", "-s", help="Split to assign in target"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max examples to pull"),
    create_target: bool = typer.Option(
        False, "--create", "-c", help="Create target dataset if it doesn't exist",
    ),
):
    """Pull examples from an existing LangSmith dataset and convert to TestCase format.

    Use this to sync external datasets (not created by this system) into the
    evaluation pipeline.  Without --target, it previews what would be imported.
    """
    mgr = _get_manager()

    async def _run():
        console.print(f"Pulling from LangSmith dataset '{source}'...")
        cases = await mgr.pull_external_dataset(source, limit=limit)

        if not cases:
            console.print("[yellow]No examples found in the source dataset.[/yellow]")
            return

        table = Table(title=f"Pulled from '{source}' — {len(cases)} case(s)")
        table.add_column("#", justify="right", style="dim")
        table.add_column("ID", style="dim", max_width=12)
        table.add_column("Name", style="cyan", max_width=30)
        table.add_column("Source")
        table.add_column("Has Output", justify="center")
        table.add_column("Input Preview", max_width=40)

        for i, case in enumerate(cases, 1):
            input_preview = ""
            if case.input_messages:
                input_preview = case.input_messages[-1].get("content", "")[:40]
            table.add_row(
                str(i),
                case.id[:12] if case.id else "",
                case.name[:30] if case.name else "",
                case.source,
                "Y" if case.expected_output else "-",
                input_preview,
            )
        console.print(table)

        if not target:
            console.print(
                "[dim]Preview only. Use --target <name> to save into a dataset.[/dim]"
            )
            return

        if create_target:
            try:
                await mgr.get_dataset(target)
            except Exception:
                await mgr.create_dataset(target, description=f"Pulled from {source}")
                console.print(f"[green]Created target dataset '{target}'[/green]")

        await mgr.provider.add_cases_batch(target, cases, split=split)
        console.print(
            f"[green]Saved {len(cases)} case(s) to '{target}'[/green]"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset create-case (interactive)
# ---------------------------------------------------------------------------

@dataset_app.command("create-case")
def dataset_create_case_interactive(
    dataset: str = typer.Argument(..., help="Dataset name"),
    split: str | None = typer.Option(None, "--split", "-s", help="Split to assign"),
):
    """Interactively create a test case with guided prompts."""
    from agent_eval.models.test_case import TestCase

    mgr = _get_manager()

    name = typer.prompt("Case name")
    description = typer.prompt("Description (optional)", default="")

    console.print("[dim]Add input messages. Empty content to finish.[/dim]")
    input_messages: list[dict[str, str]] = []
    while True:
        role = typer.prompt("  Role", default="user")
        content = typer.prompt("  Content")
        if not content:
            break
        input_messages.append({"role": role, "content": content})
        if not typer.confirm("  Add another message?", default=False):
            break

    if not input_messages:
        console.print("[red]At least one input message is required.[/red]")
        raise typer.Exit(1)

    expected_output = typer.prompt("Expected output (optional, enter to skip)", default="")
    expected_output = expected_output or None

    console.print("[dim]Add evaluation criteria. Empty line to finish.[/dim]")
    criteria: list[str] = []
    while True:
        c = typer.prompt("  Criterion (empty to finish)", default="")
        if not c:
            break
        criteria.append(c)

    tags_input = typer.prompt("Tags (comma-separated, optional)", default="")
    tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []

    scoring_mode = typer.prompt("Scoring mode (rule/llm/hybrid)", default="hybrid")

    case = TestCase(
        dataset_version="",
        name=name,
        description=description,
        input_messages=input_messages,
        expected_output=expected_output,
        expected_output_criteria=criteria,
        tags=tags,
        scoring_mode=scoring_mode,
        source="manual",
    )

    console.print()
    console.print(f"  Name: {case.name}")
    console.print(f"  Messages: {len(case.input_messages)}")
    console.print(f"  Expected output: {'yes' if case.expected_output else 'no'}")
    console.print(f"  Criteria: {len(case.expected_output_criteria)}")
    console.print(f"  Tags: {case.tags}")

    if not typer.confirm("Save this case?", default=True):
        raise typer.Abort()

    async def _run():
        ex_id = await mgr.add_case(dataset, case, split=split)
        console.print(f"[green]Created case '{name}' -> {ex_id}[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dataset generate (sub-command group)
# ---------------------------------------------------------------------------

generate_app = typer.Typer(name="generate", help="LLM-powered test case generation")
dataset_app.add_typer(generate_app)


def _get_llm():
    from langchain_openai import ChatOpenAI
    from agent_eval.config import settings

    kwargs: dict[str, Any] = {
        "model": settings.llm.model,
        "temperature": settings.llm.temperature,
        "max_tokens": settings.llm.max_tokens,
    }
    if settings.llm.api_key:
        kwargs["api_key"] = settings.llm.api_key
    if settings.llm.base_url:
        kwargs["base_url"] = settings.llm.base_url
    return ChatOpenAI(**kwargs)


def _get_case_generator():
    from agent_eval.data.case_generator import CaseGenerator
    return CaseGenerator(llm=_get_llm())


def _preview_cases(cases: list, title: str = "Generated cases") -> None:
    table = Table(title=f"{title} — {len(cases)} case(s)")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="cyan", max_width=30)
    table.add_column("Source")
    table.add_column("Tags", max_width=30)
    table.add_column("Input Preview", max_width=40)

    for i, case in enumerate(cases, 1):
        input_preview = ""
        if case.input_messages:
            input_preview = case.input_messages[-1].get("content", "")[:40]
        table.add_row(
            str(i),
            case.name[:30] if case.name else "",
            case.source,
            ", ".join(case.tags[:3]) if case.tags else "",
            input_preview,
        )
    console.print(table)


@generate_app.command("scenario")
def generate_from_scenario(
    dataset: str = typer.Argument(..., help="Target dataset name"),
    scenario: str = typer.Option(..., "--scenario", "-s", help="Scenario description"),
    count: int = typer.Option(5, "--count", "-n", help="Number of cases to generate"),
    context: str = typer.Option("", "--context", "-c", help="Additional context"),
    tag: list[str] | None = typer.Option(None, "--tag", "-t", help="Tags to apply"),
    split: str | None = typer.Option(None, "--split", help="Split to assign"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without saving"),
):
    """Generate test cases from a scenario description."""
    gen = _get_case_generator()
    mgr = _get_manager()

    async def _run():
        console.print(f"Generating {count} case(s) for scenario...")
        cases = await gen.generate_from_scenario(
            scenario, count=count, context=context, tags=tag
        )
        if not cases:
            console.print("[yellow]LLM returned no valid cases.[/yellow]")
            return

        _preview_cases(cases, "Generated from scenario")

        if dry_run:
            console.print("[dim]Dry run — not saved.[/dim]")
            return

        await mgr.add_cases_batch(dataset, cases, split=split)
        console.print(f"[green]Saved {len(cases)} case(s) to '{dataset}'[/green]")

    asyncio.run(_run())


@generate_app.command("mutate")
def generate_mutations(
    dataset: str = typer.Argument(..., help="Source dataset name"),
    case_id: str = typer.Option(..., "--case-id", "-i", help="Source case ID to mutate"),
    count: int = typer.Option(3, "--count", "-n", help="Number of variants"),
    strategy: str = typer.Option("mixed", "--strategy", help="rephrase|edge_case|adversarial|mixed"),
    target: str | None = typer.Option(None, "--target", help="Target dataset (default: same as source)"),
    tag: list[str] | None = typer.Option(None, "--tag", "-t", help="Additional tags"),
    split: str | None = typer.Option(None, "--split", help="Split to assign"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without saving"),
):
    """Generate variant test cases by mutating an existing case."""
    gen = _get_case_generator()
    mgr = _get_manager()

    async def _run():
        all_cases = await mgr.load_cases(dataset)
        source_case = next((c for c in all_cases if c.id.startswith(case_id)), None)
        if not source_case:
            console.print(f"[red]Case '{case_id}' not found in '{dataset}'[/red]")
            raise typer.Exit(1)

        console.print(f"Mutating case '{source_case.name}' with strategy '{strategy}'...")
        cases = await gen.generate_mutations(
            source_case, count=count, strategy=strategy, tags=tag
        )
        if not cases:
            console.print("[yellow]LLM returned no valid cases.[/yellow]")
            return

        _preview_cases(cases, f"Mutations of '{source_case.name}'")

        if dry_run:
            console.print("[dim]Dry run — not saved.[/dim]")
            return

        target_ds = target or dataset
        await mgr.add_cases_batch(target_ds, cases, split=split)
        console.print(f"[green]Saved {len(cases)} case(s) to '{target_ds}'[/green]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Legacy / top-level commands
# ---------------------------------------------------------------------------

@app.command()
def init_db():
    """Initialize database tables."""
    from agent_eval.db import init_db as _init_db

    async def _run():
        await _init_db()
        console.print("[green]Database initialized successfully.[/green]")

    asyncio.run(_run())


@app.command()
def run(
    dataset: str = typer.Argument(..., help="Dataset name"),
    agent_module: str = typer.Option(
        ..., help="Python module path for agent factory (e.g., myapp.agent:factory)"
    ),
    target_score: float = typer.Option(0.85, help="Target aggregate score"),
    max_iterations: int = typer.Option(10, help="Maximum optimization iterations"),
    concurrency: int = typer.Option(5, help="Evaluation concurrency"),
    split: str | None = typer.Option(None, help="Filter cases by split"),
    tag: list[str] | None = typer.Option(None, help="Filter cases by tag"),
):
    """Run the full optimization loop."""
    from agent_eval.config import settings
    from agent_eval.loop.controller import LoopConfig, LoopController

    async def _run():
        factory = _import_factory(agent_module)

        loop_config = LoopConfig(
            target_score=target_score,
            max_iterations=max_iterations,
        )

        evaluator, analyzer, generator, applicator = _build_components(settings, concurrency)

        controller = LoopController(
            config=loop_config,
            evaluator=evaluator,
            analyzer=analyzer,
            generator=generator,
            applicator=applicator,
        )

        mgr = _get_manager()
        test_cases = await mgr.load_cases(
            dataset,
            splits=[split] if split else None,
            tags=tag,
        )

        if not test_cases:
            console.print(f"[red]No test cases found for dataset '{dataset}'[/red]")
            raise typer.Exit(1)

        console.print(f"Loaded {len(test_cases)} test cases from '{dataset}'")
        console.print(f"Target: {target_score}, Max iterations: {max_iterations}")

        async def on_iteration(iteration, summary, config):
            _print_iteration_summary(iteration, summary)

        result = await controller.run_loop(factory, test_cases, on_iteration=on_iteration)
        _print_final_result(result)

    asyncio.run(_run())


@app.command()
def evaluate(
    dataset: str = typer.Argument(..., help="Dataset name"),
    agent_module: str = typer.Option(..., help="Python module path for agent factory"),
    concurrency: int = typer.Option(5, help="Evaluation concurrency"),
    split: str | None = typer.Option(None, help="Filter cases by split"),
    tag: list[str] | None = typer.Option(None, help="Filter cases by tag"),
):
    """Run evaluation only (no optimization loop)."""
    from agent_eval.config import settings

    async def _run():
        factory = _import_factory(agent_module)
        evaluator, *_ = _build_components(settings, concurrency)

        mgr = _get_manager()
        test_cases = await mgr.load_cases(
            dataset,
            splits=[split] if split else None,
            tags=tag,
        )

        agent = factory.create(factory.get_config())
        summary = await evaluator.evaluate_batch(agent, test_cases)
        _print_iteration_summary(1, summary)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _import_factory(module_path: str) -> Any:
    import importlib
    module_name, attr_name = module_path.rsplit(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _build_components(settings: Any, concurrency: int) -> tuple:
    from langchain_openai import ChatOpenAI

    from agent_eval.evaluation.orchestrator import EvaluationOrchestrator
    from agent_eval.evaluation.scorers.error_recovery import ErrorRecoveryScorer
    from agent_eval.evaluation.scorers.output import OutputCorrectnessScorer
    from agent_eval.evaluation.scorers.performance import PerformanceScorer
    from agent_eval.evaluation.scorers.reasoning import ReasoningQualityScorer
    from agent_eval.evaluation.scorers.tool_sequence import ToolSequenceScorer
    from agent_eval.optimization.failure_analyzer import FailureAnalyzer
    from agent_eval.optimization.strategy_applicator import StrategyApplicator
    from agent_eval.optimization.strategy_generator import StrategyGenerator

    judge_llm = ChatOpenAI(
        model=settings.llm.judge_model,
        temperature=0.0,
        api_key=settings.llm.api_key or None,
    )

    scorers = [
        OutputCorrectnessScorer(llm=judge_llm),
        ToolSequenceScorer(),
        ReasoningQualityScorer(llm=judge_llm),
        PerformanceScorer(),
        ErrorRecoveryScorer(llm=judge_llm),
    ]

    evaluator = EvaluationOrchestrator(scorers=scorers, concurrency=concurrency)
    analyzer = FailureAnalyzer(llm=judge_llm)
    generator = StrategyGenerator(llm=judge_llm)
    applicator = StrategyApplicator()

    return evaluator, analyzer, generator, applicator


def _print_iteration_summary(iteration: int, summary: Any) -> None:
    table = Table(title=f"Iteration {iteration} Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Aggregate Score", f"{summary.aggregate_score:.4f}")
    table.add_row("Pass Rate", f"{summary.pass_rate:.1%}")
    table.add_row(
        "Total / Passed / Failed",
        f"{summary.total_cases} / {summary.passed_cases} / {summary.failed_cases}",
    )

    for dim, avg in summary.dimension_averages.items():
        table.add_row(f"  {dim}", f"{avg:.4f}")

    console.print(table)


def _print_final_result(result: Any) -> None:
    console.print()
    if result.converged:
        console.print(f"[bold green]Converged after {result.iterations} iterations![/bold green]")
    else:
        console.print(
            f"[bold yellow]Stopped after {result.iterations} iterations: "
            f"{result.stop_reason}[/bold yellow]"
        )

    console.print(f"Final score: {result.final_score:.4f}")
    console.print(f"Best score:  {result.best_score:.4f}")
    console.print(f"Strategies applied: {len(result.optimization_history)}")


@app.command()
def server(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload for development"),
):
    """Start the FastAPI server."""
    import uvicorn
    uvicorn.run(
        "agent_eval.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app()
