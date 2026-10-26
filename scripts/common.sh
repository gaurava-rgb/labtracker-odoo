# shared by the helper scripts: cd to the bundle folder and read ODOO_DB without printing anything
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ -f .env ] || { echo "No .env here. Run ./make-env.sh first."; exit 1; }
ODOO_DB=$(grep -E '^ODOO_DB=' .env | cut -d= -f2- | tr -d '"' ); ODOO_DB=${ODOO_DB:-labtracker}
