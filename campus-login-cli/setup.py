from pathlib import Path

from setuptools import setup


setup(
    name="campus-login-cli",
    version="1.0.0",
    description="Cross-platform Dr.COM ePortal campus network CLI",
    long_description=Path(__file__).with_name("README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    py_modules=["campus_login_cli"],
    python_requires=">=3.9",
    entry_points={"console_scripts": ["campus-login=campus_login_cli:main"]},
)
