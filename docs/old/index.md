# MagScope Documentation

Welcome to the MagScope developer documentation. These guides expand on the
high-level project overview in the repository README and focus on the internals
that contributors rely on when extending the platform. Documentation is written
in Markdown so it can be rendered locally with MkDocs or directly on GitHub.

## Getting started

* Read the [Project Overview](../README.md#project-overview) for a tour of the
  major subsystems and installation instructions.
* Explore the [MagScope orchestrator guide](scope.md) to understand how the
  central `MagScope` class wires managers, shared memory, and inter-process
  communication together.
* Browse the `magscope/` package for inline docstrings that describe individual
  classes and helper functions.

## Building the docs locally

MagScope uses [MkDocs](https://www.mkdocs.org/) for a lightweight documentation
site that mirrors the Markdown files in this directory. To preview the docs:

```bash
pip install mkdocs
mkdocs serve
```

Then open http://127.0.0.1:8000/ in a browser to browse the rendered site.

Run `mkdocs build` to generate a static site in the `site/` directory suitable
for deployment to GitHub Pages or another static host.

## Contributing

When you add new features or subsystems, prefer creating a dedicated Markdown
file under `docs/` and linking it from the navigation in `mkdocs.yml`. Keep
inline module docstrings focused on API reference material while these guides
cover workflows, examples, and diagrams.
