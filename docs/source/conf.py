# python -m sphinx -b html docs/source docs/_build/html

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

project = 'MagScope'
copyright = '2025, James London'
author = 'James London'
release = '0.2.1'

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx_autodoc_typehints",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_design",
    'autoapi.extension',
]

autoapi_type = "python"
autoapi_dirs = ["../../magscope"]
autoapi_add_toctree_entry = True
autoapi_root = "api"

autodoc_mock_imports = ["cupy"]

templates_path = ['_templates']
exclude_patterns = []

html_theme = 'sphinx_book_theme'
html_static_path = ['_static']
html_logo = "../../assets/logo.png"

html_theme_options = {
    "repository_url": "https://github.com/7jameslondon/MagScope",
    "use_repository_button": True,
    "path_to_docs": "docs/source",
}

autosummary_generate = True
napoleon_google_docstring = False
napoleon_numpy_docstring = True