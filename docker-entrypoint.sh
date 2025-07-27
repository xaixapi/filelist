#!/bin/sh
FILE_PATH=/home/ywgx/1/filelist
if ping filelist-redis -c 1 &>/dev/null;then
    $FILE_PATH/filelist.py --auth=true --port=3006 --root=$FILE_PATH/files
else
    $FILE_PATH/filelist.py --auth=false --port=8000 --root=$FILE_PATH/files
fi
