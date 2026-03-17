import os

from . import models


def _post_init_hook(env):
    """Activate the book data cron only in production on Odoo.sh."""
    cron = env.ref('book_data.ir_cron_refresh_book_data', raise_if_not_found=False)
    if cron:
        cron.active = os.environ.get('ODOO_STAGE') == 'production'
