<h1 align="center">
<img src="https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/logo.png" width="300">
</h1><br>

[![PyPi](https://img.shields.io/pypi/v/magscope.svg)](https://pypi.org/project/magscope/)
[![Docs](https://img.shields.io/readthedocs/magscope/latest.svg)](https://magscope.readthedocs.io)
[![Python package](https://github.com/7jameslondon/MagScope/actions/workflows/python-package.yml/badge.svg)](https://github.com/7jameslondon/MagScope/actions/workflows/python-package.yml)
[![Paper](https://img.shields.io/badge/DOI-10.1101/2025.10.31.685671-blue)](https://doi.org/10.1101/2025.10.31.685671)

[MagScope](https://github.com/7jameslondon/MagScope) is a Python-based application for live data acquisition and analysis in [magnetic tweezers microscopy](https://doi.org/10.1007/978-1-0716-3377-9_18). 

* Fast, high-throughput, and high-resolution
* GUI - includes a clean simple GUI (Graphical User Interface)
* Demo - Launches by default with a simulated camera so you can try it without microscope hardware connected
* Automation - Create simple Python scripts to automate data-collection and motor movement for long/complex experiments.
* XYZ-Lock - Enable XY- and/or Z-Lock to keep beads centered and in focus for long experiments
* Customizable - Easily add your lab's hardware and implement custom features
* CPU or GPU tracking of beads via [MagTrack](https://github.com/7jameslondon/MagTrack)

## 🚀 Getting Started
[👉 👉 👉 Get Started Here 👈 👈 👈](https://magscope.readthedocs.io)

## 📖 Documnetation
View the full guide to MagScope at [magscope.readthedocs.io](https://magscope.readthedocs.io)

## 💬 Support
Report issues and make requests on the [GitHub issue tracker](https://github.com/7jameslondon/MagScope/issues)<br><br>
Having trouble? Need help? Have suggestions? Want to contribute?<br>
Email us at magtrackandmagscope@gmail.com

## ⚒ Quick Start (Advanced)
<details>
  <summary>Click to expand</summary>

Easy CPU only install with pip:
```
pip install magscope
```
Launch the demo with:
```
import magscope

scope = magscope.MagScope()
scope.start()
```
More details on how to install with GPU-acceleration, connect your own camera and more are in the [documentation](https://magscope.readthedocs.io)!
</details>
