from setuptools import setup, find_packages

setup(
    name="xiaozhi-client",
    version="0.1.1",
    packages=find_packages(),
    install_requires=[
        "websockets>=10.0,<11.0",  # 降级到 10.x 版本
        "opuslib>=3.0.1",
        "numpy>=1.26.4",
        "sounddevice>=0.4.6",  # 添加音频播放依赖
        "loguru>=0.7.0", # 添加loguru依赖
        "pyaudio==0.2.14",  # 添加pyaudio依赖
    ],
    author="Eric",
    author_email="eric230308@gmail.com",
    description="A Python client library for Xiaozhi AI assistant",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/eric0308/xiaozhi-client",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
)
