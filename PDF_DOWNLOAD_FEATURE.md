# PDF Download Feature Implementation

## Overview
This feature allows users to download previously submitted invoice PDFs either individually or in bulk. PDFs are automatically stored when invoices are submitted to FBR.

## What Was Changed

### 1. Database Changes
- **Added column**: `pdf_data BYTEA` to `invoices` table
- **Added index**: For faster queries on invoices with PDFs
- **Storage**: PDFs stored as binary data directly in PostgreSQL

### 2. Backend Changes

#### `app.py`
- **Modified `/submit-fbr` endpoint**: Now generates and stores PDF when submitting via Excel upload
- **Modified `/api/generate-form-invoice` endpoint**: Stores PDF when generating from form submission
- **PDF Generation**: Uses same templates and logic as existing invoice generation

#### `reports_routes.py`
- **New endpoint**: `GET /api/reports/downloadable-invoices` - Returns list of invoices with stored PDFs
- **New endpoint**: `GET /api/reports/download-invoice/<id>` - Downloads single invoice PDF
- **New endpoint**: `POST /api/reports/download-invoices-bulk` - Downloads multiple PDFs as ZIP
- **Added imports**: `send_file`, `BytesIO`, `zipfile`

### 3. Frontend Changes

#### `reports.html`
- **New Tab**: "Download Invoices" tab added after "Buyers" tab
- **Features**:
  - Filter by environment (Sandbox/Production)
  - Filter by date range
  - Search by invoice reference or buyer name
  - Select multiple invoices with checkboxes
  - Download individual PDFs
  - Bulk download as ZIP file
  - Environment badges (color-coded)
  - Responsive table design

## How It Works

### For New Invoices (After Implementation)
1. User submits invoice to FBR (via Excel or Form)
2. System generates PDF automatically
3. PDF is stored in `invoices.pdf_data` column
4. PDF becomes available in "Download Invoices" section

### For Old Invoices (Before Implementation)
- Old invoices do NOT have stored PDFs
- They continue to work with existing "Get Invoice PDF" button
- No changes to existing functionality

## User Flow

### Single Download
1. Go to Reports & Analytics → Download Invoices tab
2. Find the invoice in the table
3. Click "Download" button
4. PDF downloads immediately

### Bulk Download
1. Go to Reports & Analytics → Download Invoices tab
2. Select multiple invoices using checkboxes
3. Click "Download Selected (ZIP)" button
4. All selected PDFs download as a single ZIP file

## Important Notes

### Storage
- **Approach**: Database storage (simple, no additional setup)
- **Size**: ~50-100 KB per PDF
- **Capacity**: Well within Supabase free tier limits (500 invoices/month = ~50 MB)

### Backward Compatibility
- ✅ Existing invoice generation unchanged
- ✅ Dashboard "Get Invoice PDF" still works
- ✅ Old invoices work as before
- ✅ No disruption to current workflows

### Performance
- PDFs stored only for successful submissions
- Indexed for fast retrieval
- Bulk download uses ZIP compression

## Testing Checklist

- [ ] Submit new invoice via Excel upload
- [ ] Submit new invoice via form
- [ ] Check "Download Invoices" tab shows new invoices
- [ ] Download single invoice PDF
- [ ] Select multiple invoices and download as ZIP
- [ ] Filter by environment (Sandbox/Production)
- [ ] Filter by date range
- [ ] Search functionality
- [ ] Verify environment badges display correctly
- [ ] Test with both sandbox and production invoices

## Future Enhancements (Optional)

1. **PDF Regeneration**: Add ability to regenerate PDFs for old invoices
2. **Delete PDFs**: Add option to delete stored PDFs to save space
3. **Storage Stats**: Show total storage used by PDFs
4. **Email PDFs**: Send PDFs via email
5. **PDF Preview**: View PDF in browser before downloading

## Rollback Instructions

If needed, to rollback:

```sql
-- Remove the pdf_data column
ALTER TABLE invoices DROP COLUMN pdf_data;

-- Restore original endpoints by commenting out the new routes in reports_routes.py
-- Remove the Download Invoices tab from reports.html
```
