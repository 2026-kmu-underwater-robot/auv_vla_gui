from glob import glob

from setuptools import setup

package_name = "kmu26_auv_vla_gui"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml", "README.md"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
        (f"share/{package_name}/scripts", glob("scripts/*.sh")),
        (f"share/{package_name}/web", glob("web/*")),
    ],
    install_requires=["setuptools", "fastapi", "uvicorn", "websockets"],
    zip_safe=True,
    maintainer="kuuve",
    maintainer_email="kuuve@todo.todo",
    description="Launch-oriented web GUI for KMU26 AUV VLA operation.",
    license="Apache-2.0",
    entry_points={"console_scripts": ["server = kmu26_auv_vla_gui.server:main"]},
)
