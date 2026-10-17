import typer.main

from main import app


def test_cli_commands_build():
    # Building the Click command parses every option annotation; Typer 0.12 raises
    # "Type not yet supported" for `X | None`, which crashed `serve` on the Pi.
    command = typer.main.get_command(app)
    assert {"generate", "serve"} <= set(command.commands)
