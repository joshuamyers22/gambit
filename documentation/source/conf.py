"""Sphinx configuration for the Gambit documentation."""

from importlib.metadata import version as distribution_version

project = "Gambit"
author = "Gambit contributors"
copyright = "2018–2026, Sal Abbasi and Gambit contributors"
release = distribution_version("gambit-markets")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.doctest",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
]
autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

root_doc = "index"
source_suffix = ".rst"
exclude_patterns = ["gambit.rst", "modules.rst"]
templates_path = ["_templates"]

html_theme = "pydata_sphinx_theme"
html_title = f"Gambit {release}"
html_static_path = []
html_theme_options = {
    "header_links_before_dropdown": 6,
    "show_toc_level": 2,
    "use_edit_page_button": True,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/joshuamyers22/gambit",
            "icon": "fa-brands fa-github",
        }
    ],
}
html_context = {
    "github_user": "joshuamyers22",
    "github_repo": "gambit",
    "github_version": "main",
    "doc_path": "documentation/source",
}

htmlhelp_basename = "gambitdoc"
