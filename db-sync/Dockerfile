FROM mariadb:11

# La imagen mariadb:11 ya trae mariadb-dump y mariadb client, asi que solo
# copio el script y lo dejo como entrypoint.
COPY sync.sh sync-now.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/sync.sh /usr/local/bin/sync-now.sh

ENTRYPOINT ["/usr/local/bin/sync.sh"]
