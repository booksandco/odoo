-- deactivate book data cron on neutralized (staging/testing) databases
UPDATE ir_cron
   SET active = false
 WHERE id IN (
       SELECT res_id
         FROM ir_model_data
        WHERE model = 'ir.cron'
          AND name = 'ir_cron_refresh_book_data'
          AND module = 'book_data'
);
