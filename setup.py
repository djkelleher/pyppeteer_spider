import setuptools

setuptools.setup(
    name='distbot',
    version='2.8.9',
    author='Dan Kelleher',
    author_email='danielkelleher@protonmail.com',
    maintainer='Dan Kelleher',
    maintainer_email='danielkelleher@protonmail.com',
    include_package_data=True,
    description='Distributed (browser-based) web scraping in Python with automatic error handling and recovery and detection prevention.',
    packages=['distbot'],
    url='https://github.com/djkelleher/distbot',
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'License :: OSI Approved :: MIT License',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Operating System :: OS Independent', 'Topic :: Internet',
        'Topic :: Internet :: WWW/HTTP :: Browsers',
        'Topic :: Scientific/Engineering :: Information Analysis'
    ],
    install_requires=[
        'pyppeteer',
        'requests',
        'html_text'
    ],
    test_requires=['pytest', 'pytest_asyncio'])
