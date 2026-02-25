# Book Data Module

A module that enhances product management for bookstores by fetching book metadata from external APIs when ISBN barcodes are entered.

## Features

- **Hardcover API Integration**: Fetches comprehensive book metadata from the Hardcover API
- **Automatic Data Population**: Populates the following fields on products when ISBN is entered:
  - Title
  - Description (HTML formatted)
  - Author(s)
  - Publisher
  - Publication Date
  - Book Cover Image

## How It Works

1. **Automatic Trigger**: When an ISBN-13 barcode (starting with 978) is entered or updated on a product, the module automatically fetches data
2. **API Request**: The module queries the Hardcover GraphQL API with the ISBN
3. **Data Parsing**: Response data is parsed and formatted for Odoo fields
4. **Field Population**: Only empty fields are populated (existing data is never overwritten)
5. **User Notification**: A popup message appears showing:
   - Success message listing which fields were populated
   - "Book Not Found" if the ISBN doesn't exist on Hardcover
   - "API Not Configured" if the API key is not set
   - Error message if the API request fails

## Setup

### 1. Get a Hardcover API Key

1. Visit [hardcover.app](https://hardcover.app)
2. Create an account or log in
3. Go to Account Settings → API
4. Generate an API key
5. Copy the token

### 2. Configure the API Key

1. In Odoo, go to **Settings** (⚙️ icon in the top-right)
2. Search for "Hardcover" or navigate to **Inventory** → **Settings**
3. Enter your Hardcover API key in the "Hardcover API Key" field
4. Click **Save**

### 3. Use the Module

1. Go to **Inventory** → **Products** → **Products**
2. Open or create a product
3. Enter an ISBN-13 barcode (format: 978XXXXXXXXXXX)
4. Move to the next field - a popup will appear showing what happened:
   - Which fields were populated (title, author, publisher, etc.)
   - Or an error/warning message if something went wrong
5. The product is automatically updated with the fetched data

## Custom Fields

The module uses the following custom fields (defined in the bookstore module):

- `x_author`: Author name(s)
- `x_publisher`: Publisher name
- `x_publication_date`: Publication date

## Dependencies

- **bookstore**: Base module containing custom field definitions
- Requires internet connectivity to access the Hardcover API

## Notes

- Only ISBN-13 barcodes (starting with 978) are supported
- Existing field values are never overwritten; only empty fields are populated
- API requests include a 10-second timeout
- Images are downloaded and stored as base64 in the product
- Fetching happens automatically via the `@api.onchange('barcode')` handler
- User is notified via popup messages about success, errors, or missing configuration
- If the API key is not configured, the user is prompted to add it
