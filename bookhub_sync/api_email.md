Hi Tomlin

I am reaching out regarding the API sync app that allows synchronisation between our website (booksandco.co.nz) and the shadow circle site (bco.circlepos.com).
We use Odoo, which if you're not familiar is an all in one eCommerce platform. 
I am interested in the capabilities/scope of the sync app, and if Odoo integration is not currently availably, happy to contribute to developing it.

Thanks,
Harry



1 / 216 Main Highway
Ōtaki 5512
Aotearoa New Zealand
06 262 9158
otaki@booksandco.co.nz       www.booksandco.co.nz
Mon-Fri 9am - 5pm; Sat 10am - 4pm; Sun 10am - 3pm






Little Ventures <contact@littleventures.net>
Jun 11, 2026, 9:39 AM
to Otaki

Hi Harry,

Thanks for getting in touch.

I have done a small amount of work with Odoo in the past, but not ecommerce related.

In order to read inventory from your store, we would need API access. There is an important note regarding this on the Odoo site:

"Access to data via the external API is only available on Custom Odoo pricing plans. Access to the external API is not available on One App Free or Standard plans. For more information visit the Odoo pricing page or reach out to your Customer Success Manager."

If you are hosting your own instance this is probably not the case.

In order to give you a reasonable estimate of the time it might take to include Odoo support in the Bookhub sync app for your store I would need to make use of the Odoo API to investigate the model schema you are using - i.e. I would need API access to allow some initial exploration. If you are able to provide that I can do my best to get you an estimate within the next week or two.

Any questions, feel free to give me a call or send an email.


Kind regards,
Tomlin
_______________
Tomlin Leniston
+64 22 461 8485

Little Ventures



Books and Co <nz@booksandco.co.nz>
Jun 11, 2026, 11:13 AM
to Harry


Books and Co <nz@booksandco.co.nz>
Jul 7, 2026, 11:33 AM
to Little

Hi Tomlin,

Apologies for the slow reply, have been rather busy and just gotten around to following up on this.
I've created an api key, and got AI to test that it has access to what you need using the docs https://www.odoo.com/documentation/19.0/developer/reference/external_api.html - here's the summary it generated:

   Connection details
   • Host: booksco.odoo.com
   • Database: booksandco-main-29072492
   • Login: booksco@harrybird.nz
   • Key: f88751952083c0f30a46ac1c5af78f7a2484b86c
   • Protocol: XML-RPC / JSON-RPC at https://booksco.odoo.com/xmlrpc/2 and /jsonrpc
   • Authentication: works, UID 17

   What you can access
   The key has read access to the product and inventory models you need:

   • product.template — 26,590 products, including:
       • name
       • default_code (ISBN)
       • list_price (sales price)
       • standard_price (cost price)
       • website_published and is_published
       • website_url (the shop slug, e.g. /shop/9781761620003  -wild-dark-shore-52154)
       • product_variant_ids (links to product.product)
       • categ_id, barcode, active, create_date, write_date

   • product.product — 26,590 product variants

   • stock.quant — 17,136 stock records with product_id, location_id, warehouse_id, quantity, available_quantity,
     reserved_quantity

   • stock.location — 7 locations

   7,224 products are currently website-published.

   What you cannot access
   • ir.model and ir.model.fields are blocked, so you can't globally list models or introspect via the settings tables. Use
     fields_get on each known model instead.
   • No read access to sales orders, invoices, purchases, stock pickings, or POS orders.
   • No write/create/delete access to products, partners, or stock.
   • Some computed fields on products trigger access errors if requested (e.g. stock-move-related fields), so stick to direct fields.

   Suggested sync query
   1. Fetch published products from product.template where website_published = True.
   2. Join to product.product via product_variant_ids.
   3. Join to stock.quant via product_id and sum available_quantity per product/warehouse.
   4. Use website_url as the redirect slug.

   Important caveats
   • The key is read-only for products and stock. You won't be able to update Odoo from the third-party site.
   • Odoo SaaS rate limits apply; batch reads and use search_read with explicit field lists.
   • Some fields don't exist on res.partner in this install (e.g. mobile), so request only fields returned by fields_get.


   Thanks,
   Harry

Little Ventures
Jul 13, 2026, 9:33 AM
to Otaki

Thanks very much for this Harry. Very helpful.

I should have time to explore the API and estimate the time it might take to update the app to include the custom fetch functions for interacting with your store before the end of this week.

I'm going to assume that there is no notification/push/webhook functionality on the site and will have to rely on regular polling for inventory changes. The polling cadence will have to be determined based on some experimentation. There are a decent number of products on the site so a full reconciliation could take some time - meaning this might not be practicable too often.

With this in mind, I do have one initial question: how often (if ever) do items have their 'website_published' value change? If this is infrequent I would probably take the approach of only regularly checking items where this is true, and then checking the full inventory only each 24 hours to pick up any new items or those where the 'website_published' value has switched to true (those switching to a value of false would be picked up more quickly with this approach).


Kind regards,
Tomlin

Books and Co <nz@booksandco.co.nz>
Jul 13, 2026, 9:38 AM
to Harry





1 / 216 Main Highway
Ōtaki 5512
Aotearoa New Zealand
06 262 9158
otaki@booksandco.co.nz       www.booksandco.co.nz
Mon-Fri 9am - 5pm; Sat 10am - 4pm; Sun 10am - 3pm







---------- Forwarded message ---------
From: Little Ventures <contact@littleventures.net>
Date: Mon, Jul 13, 2026 at 11:33 AM
Subject: Re: BookHub Sync App login
To: <Otaki@booksandco.co.nz>


       • website_url (the shop slug, e.g. /shop/9781761620003   -wild-dark-shore-52154)

Books and Co <nz@booksandco.co.nz>
Jul 13, 2026, 10:33 AM
to Little

Hi Tomlin
Odoo can send webhooks as part of its automated actions, a trigger of which can be when the product values (quantity/price/status) are updated.
https://www.odoo.com/documentation/19.0/applications/studio/automated_actions.html#values-updated
https://www.odoo.com/documentation/19.0/applications/studio/automated_actions.html#send-webhook-notification
I can set this up at our end, if you let me know what fields/format you want the POST request to take.

Little Ventures
Jul 13, 2026, 10:36 AM
to Otaki

Fantastic. I'll plan the update around this and let you know when I get to the implementation stage - when I will be able to provide you with an endpoint/s for the webhook/s.


Kind regards,
Tomlin

Books and Co <nz@booksandco.co.nz>
Aug 3, 2026, 9:26 AM (3 days ago)
to Little

Hi Tomlin,

Any updates on how this is progressing?

Little Ventures
Aug 5, 2026, 8:00 AM (1 day ago)
to Otaki

Good morning Harry,

Apologies for the delay in getting back to you.

I estimate that this will likely take between 6 and 10 hours to implement. Given that this would be done on behalf of Booksellers, I would be happy to price this at their discounted rate of $85/h ex GST.

As with most software projects there is always some uncertainty involved and to accommodate this and to give you some assurance I can suggest two options for pricing:
A flat fee of $750 ex GST
A price based on actual time spent at $85/h ex GST, capped at 10 hours - i.e. $85 per hour of actual work with a guarantee of not charging above the upper estimate of 10 hours.
To be clear about what would be delivered, we are discussing development, testing, and deployment of changes to the Booksellers Sync app to accommodate the inclusion of your Odoo store inventory. As with existing platforms supported by the Sync app, this essentially involves automated retrieval and parsing of your store data, and the subsequent inclusion of this data on the Bookhub website.

Because this would be implemented as an update to the Bookhub Sync application owned by Booksellers, future maintenance and updates would be handled as part of the ongoing maintenance contract for that application - i.e. without further cost to you. The exception would be if significant breaking changes are made to your platform (e.g. moving away from Odoo, changes made to the data schema used on Odoo, etc).

Let me know if you have any questions and how you would like to proceed from here.


Kind regards,
Tomlin
