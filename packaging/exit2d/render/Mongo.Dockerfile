FROM mongo:7.0.16@sha256:c630c59342c1493d50345136df2af14a76b9e827dd5316bfabee07a0880a5f3a
COPY packaging/exit2d/render/mongo-start.sh /opt/mongo-start.sh
ENTRYPOINT ["bash", "/opt/mongo-start.sh"]
