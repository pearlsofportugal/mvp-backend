"""Prompt template loader — centralises all Jinja2 prompt rendering."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=StrictUndefined,   # raises immediately if a variable is missing
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_prompt(template_path: str, **kwargs: object) -> str:
    """Render a Jinja2 prompt template.

    Args:
        template_path: Relative path from the prompts/ directory, e.g.
                       "seo_copywriting/v2.j2"
        **kwargs: Variables injected into the template.

    Returns:
        Rendered prompt string ready to be sent to the AI model.

    Raises:
        jinja2.UndefinedError: If a required template variable is missing.
        jinja2.TemplateNotFound: If the template file does not exist.
    """
    template = _env.get_template(template_path)
    return template.render(**kwargs)