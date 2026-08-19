import datetime
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('account', '0001_initial'),
        ('observability', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AIInvestigation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('correlation_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('question', models.TextField()),
                ('requested_host_ids', models.TextField(default='[]')),
                ('status', models.CharField(choices=[('running', '调查中'), ('completed', '已完成'), ('failed', '失败')], db_index=True, default='running', max_length=16)),
                ('provider', models.CharField(default='openai-compatible', max_length=32)),
                ('model', models.CharField(max_length=100)),
                ('prompt_version', models.CharField(max_length=32)),
                ('evidence', models.TextField(default='[]')),
                ('citations', models.TextField(default='[]')),
                ('result', models.TextField(blank=True, null=True)),
                ('error', models.CharField(blank=True, max_length=500, null=True)),
                ('input_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('output_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=datetime.datetime.now)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('alert', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_investigations', to='observability.AlertEvent')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_investigations', to='account.User')),
            ],
            options={'db_table': 'ai_investigations', 'ordering': ('-created_at',), 'index_together': {('created_by', 'status')}},
        ),
        migrations.CreateModel(
            name='AIActionPlan',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('summary', models.TextField()),
                ('risk_level', models.CharField(choices=[('low', '低风险'), ('medium', '中风险'), ('high', '高风险'), ('critical', '严重风险')], max_length=16)),
                ('steps', models.TextField(default='[]')),
                ('rollback_plan', models.TextField(blank=True, null=True)),
                ('status', models.CharField(choices=[('proposed', '仅建议'), ('dismissed', '已忽略')], db_index=True, default='proposed', max_length=16)),
                ('created_at', models.DateTimeField(db_index=True, default=datetime.datetime.now)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_action_plans', to='account.User')),
                ('investigation', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='action_plan', to='aiops.AIInvestigation')),
            ],
            options={'db_table': 'ai_action_plans', 'ordering': ('-created_at',)},
        ),
    ]
