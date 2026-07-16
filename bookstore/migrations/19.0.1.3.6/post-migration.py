from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # A hardcover_id of 0 is not a valid identifier; clear it so the unique
    # constraint does not collide with newly created records that have no ID.
    env.cr.execute(
        """
        UPDATE bookstore_author
        SET hardcover_id = NULL
        WHERE hardcover_id = 0
        """
    )
    env.cr.execute(
        """
        UPDATE bookstore_publisher
        SET hardcover_id = NULL
        WHERE hardcover_id = 0
        """
    )
