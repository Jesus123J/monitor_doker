FROM mariadb:11

# La imagen mariadb:11 ya trae mariadb-dump y mariadb client, asi que solo
# copio el script y lo dejo como entrypoint.
COPY sync.sh /usr/local/bin/sync.sh
RUN chmod +x /usr/local/bin/sync.sh

ENTRYPOINT ["/usr/local/bin/sync.sh"]
