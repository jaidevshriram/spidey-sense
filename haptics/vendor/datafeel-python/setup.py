try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

setup(
    name='datafeel',
    version='1.0.0',
    packages=['datafeel'],
    license='MIT license',
    url='https://datafeel.com',
    author='DataFeel',
    author_email='charles@datafeel.com',
    description='DataFeel Dot is a multi-energy haptic device.',
    long_description=open('README.txt').read(),
    install_requires=[
        'pyserial',
        'minimalmodbus'
    ]
)
