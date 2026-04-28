from setuptools import setup

setup(
	name="insighta-labs-cli",
	version="0.1.0",
	py_modules=["insighta_cli"],
	entry_points={
		"console_scripts": [
			"insighta=insighta_cli:main",
		],
	},
)
