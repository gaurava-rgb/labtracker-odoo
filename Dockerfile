# Lab Tracker Odoo: Odoo 20 Community + the lab_dashboard add-on + self-provisioning entrypoint.
# Build:  docker build -t labtracker-odoo:20.0 .
FROM odoo:20.0

USER root
# the add-on and the provisioning scripts (xmlrpc + psycopg2 only, both already in the base image)
COPY --chown=odoo:odoo addons/lab_dashboard /mnt/extra-addons/lab_dashboard
COPY --chown=odoo:odoo init /opt/labtracker/init
COPY --chown=odoo:odoo labtracker-entrypoint.sh /labtracker-entrypoint.sh
RUN chmod 755 /labtracker-entrypoint.sh \
 && find /mnt/extra-addons /opt/labtracker -name '__pycache__' -prune -exec rm -rf {} + \
 && chown odoo:odoo /etc/odoo/odoo.conf

LABEL org.opencontainers.image.title="labtracker-odoo" \
      org.opencontainers.image.description="Lab Tracker on Odoo 20: project/maintenance/crm + Lab Home dashboard, self-provisioning" \
      org.opencontainers.image.version="20.0"

USER odoo
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=5 \
  CMD curl -fsS http://localhost:8069/web/health || exit 1
ENTRYPOINT ["/labtracker-entrypoint.sh"]
CMD []
