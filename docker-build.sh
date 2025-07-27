#!/bin/sh
#*************************************************
# Description : /filelist/build.sh
# Version     : 2.0
#*************************************************
#-----------------VAR-----------------------------
SRC=/filelist
#-----------------FUN-----------------------------
main(){
        find $SRC -name "*.pyc" -delete
        cd $SRC
        for pyx in `find . -maxdepth 3 -mindepth 1 -name "*.py"  -a ! -name "__init__.py"`
        do
                cat << EOF > setup.py
# cython: language_level=3
from setuptools import setup, Extension
from Cython.Build import cythonize
ext_options = {"compiler_directives": {"language_level": "3"}}
setup(
    ext_modules = cythonize("$pyx", **ext_options)
)
EOF
                python setup.py build_ext --inplace
        done
        for i in `find . -name "*.so"`;do mv $i `echo ${i##*/.}|awk -F '.' '{print "."$2".so"}'`;done
        rm -rf build
        find . -maxdepth 3 -mindepth 1 -name "*.c" -delete
        find . -maxdepth 3 -mindepth 1 -name "*.py*"  -a ! -name "__init__.py" -delete
        cat << EOF > filelist.py
#!/usr/local/bin/python
from index import main
if __name__ == '__main__':
    main()
EOF
        chmod +x filelist.py
}
#-----------------PROG----------------------------
main
