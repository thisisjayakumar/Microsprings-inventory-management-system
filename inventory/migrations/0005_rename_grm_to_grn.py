# Generated migration for GRM to GRN renaming

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_alter_inventorytransaction_reference_type'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        # Rename the GRMReceipt model to GRNReceipt
        migrations.RenameModel(
            old_name='GRMReceipt',
            new_name='GRNReceipt',
        ),
        
        # Rename grm_number field to grn_number in GRNReceipt
        migrations.RenameField(
            model_name='grnreceipt',
            old_name='grm_number',
            new_name='grn_number',
        ),
        
        # Rename grm_receipt field to grn_receipt in HeatNumber
        migrations.RenameField(
            model_name='heatnumber',
            old_name='grm_receipt',
            new_name='grn_receipt',
        ),
        
        # Rename grm_number field to grn_number in InventoryTransactionHeat
        migrations.RenameField(
            model_name='inventorytransactionheat',
            old_name='grm_number',
            new_name='grn_number',
        ),
    ]

