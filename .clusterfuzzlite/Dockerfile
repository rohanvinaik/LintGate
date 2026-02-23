FROM gcr.io/oss-fuzz-base/base-builder-python

COPY . $SRC/lintgate
WORKDIR $SRC/lintgate

COPY .clusterfuzzlite/build.sh $SRC/build.sh
