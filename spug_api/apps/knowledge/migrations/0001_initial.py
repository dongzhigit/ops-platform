import datetime
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('account', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='KnowledgeSpace',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('slug', models.CharField(max_length=64, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('visibility', models.CharField(choices=[('private', '仅成员可见'), ('internal', '组织内可见')], db_index=True, default='private', max_length=16)),
                ('retention_days', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(db_index=True, default=datetime.datetime.now)),
                ('updated_at', models.DateTimeField(default=datetime.datetime.now)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_knowledge_spaces', to='account.User')),
            ],
            options={'db_table': 'knowledge_spaces', 'ordering': ('name', 'id')},
        ),
        migrations.CreateModel(
            name='KnowledgeMembership',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('viewer', '查看者'), ('editor', '编辑者'), ('manager', '管理员')], default='viewer', max_length=16)),
                ('created_at', models.DateTimeField(default=datetime.datetime.now)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_knowledge_memberships', to='account.User')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='knowledge.KnowledgeSpace')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='knowledge_memberships', to='account.User')),
            ],
            options={'db_table': 'knowledge_memberships', 'ordering': ('space_id', 'user__nickname'), 'unique_together': {('space', 'user')}},
        ),
        migrations.CreateModel(
            name='KnowledgeDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('slug', models.CharField(max_length=100)),
                ('kind', models.CharField(choices=[('article', '文档'), ('runbook', 'Runbook'), ('postmortem', '故障复盘'), ('asset', '资产说明')], db_index=True, default='article', max_length=16)),
                ('status', models.CharField(choices=[('draft', '草稿'), ('review', '待审核'), ('published', '已发布'), ('archived', '已归档')], db_index=True, default='draft', max_length=16)),
                ('summary', models.CharField(blank=True, max_length=1000, null=True)),
                ('content', models.TextField(default='')),
                ('tags', models.TextField(default='[]')),
                ('version', models.PositiveIntegerField(default=1)),
                ('content_hash', models.CharField(max_length=64)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=datetime.datetime.now)),
                ('updated_at', models.DateTimeField(db_index=True, default=datetime.datetime.now)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_knowledge_documents', to='account.User')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='account.User')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='documents', to='knowledge.KnowledgeSpace')),
                ('updated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='updated_knowledge_documents', to='account.User')),
            ],
            options={'db_table': 'knowledge_documents', 'ordering': ('-updated_at', '-created_at'), 'unique_together': {('space', 'slug')}, 'index_together': {('space', 'status'), ('space', 'kind')}},
        ),
        migrations.CreateModel(
            name='KnowledgeDocumentRevision',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('version', models.PositiveIntegerField()),
                ('title', models.CharField(max_length=200)),
                ('kind', models.CharField(choices=[('article', '文档'), ('runbook', 'Runbook'), ('postmortem', '故障复盘'), ('asset', '资产说明')], max_length=16)),
                ('status', models.CharField(choices=[('draft', '草稿'), ('review', '待审核'), ('published', '已发布'), ('archived', '已归档')], max_length=16)),
                ('summary', models.CharField(blank=True, max_length=1000, null=True)),
                ('content', models.TextField(default='')),
                ('tags', models.TextField(default='[]')),
                ('content_hash', models.CharField(max_length=64)),
                ('change_note', models.CharField(blank=True, max_length=255, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=datetime.datetime.now)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='knowledge_revisions', to='account.User')),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='revisions', to='knowledge.KnowledgeDocument')),
            ],
            options={'db_table': 'knowledge_document_revisions', 'ordering': ('-version',), 'unique_together': {('document', 'version')}},
        ),
    ]
