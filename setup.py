from setuptools import setup, find_packages

setup(
    name="garix",
    version="1",
    packages=find_packages(),
    entry_points={
        "console_scripts": ["garix = garix:main"]
        },
    install_requires=[
        'Flask>=0.12.2',
        'RPi.GPIO==0.7.1a4',
        'click==8.1.7'
        ]
    )

