"""Regression: production legacy reset table lacks created_by_user_id."""
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, select, inspect
from sqlalchemy.orm import sessionmaker
from app import main
from app.db import Base, get_db
from app.models import User, PasswordResetToken

class PasswordResetMigrationTest(unittest.TestCase):
 def test_legacy_database_and_complete_reset_flow(self):
  with tempfile.TemporaryDirectory() as tmp:
   engine=create_engine('sqlite:///'+tmp+'/legacy.db',connect_args={'check_same_thread':False})
   sessions=sessionmaker(bind=engine,autoflush=False)
   with engine.begin() as conn:
    conn.execute(text('''CREATE TABLE password_reset_tokens (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
      token_hash VARCHAR(64) NOT NULL UNIQUE, expires_at TIMESTAMP NOT NULL,
      used_at TIMESTAMP, created_at TIMESTAMP NOT NULL)'''))
   Base.metadata.create_all(engine)
   with sessions() as db:
    supplier=User(name='Fornecedor teste',email='reset-supplier@test.local',role='supplier',active=True,
      password_hash=main.hash_password('OldPassword123!'),must_change_password=True)
    db.add(supplier);db.commit();sid=supplier.id
    db.execute(text('INSERT INTO password_reset_tokens (user_id,token_hash,expires_at,created_at) VALUES (:uid,:hash,:expiry,:created)'),
      {'uid':sid,'hash':main.token_digest('legacy-token'),'expiry':datetime.utcnow()+timedelta(hours=1),'created':datetime.utcnow()})
    db.commit()
   def dependency():
    with sessions() as db:yield db
   main.app.dependency_overrides[get_db]=dependency
   try:
    with patch.object(main,'engine',engine), patch.object(main,'SessionLocal',sessions), patch.dict(os.environ,{'ADMIN_EMAIL':'reset-admin@test.local','ADMIN_PASSWORD':'AdminTest123!','PUBLIC_BASE_URL':'https://cotacao.example'}):
     # The previous code skipped this migration: reproduce the exact 500.
     with patch.object(main,'ensure_runtime_schema',lambda:None):
      with TestClient(main.app,raise_server_exceptions=False) as client:
       client.post('/login',data={'email':'reset-admin@test.local','password':'AdminTest123!'})
       self.assertEqual(client.post(f'/admin/fornecedores/{sid}/reset-senha').status_code,500)
     with TestClient(main.app) as client:
      self.assertIn('created_by_user_id',{c['name'] for c in inspect(engine).get_columns('password_reset_tokens')})
      main.ensure_runtime_schema(); main.ensure_runtime_schema() # repeat safely
      with sessions() as db:
       old=db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash==main.token_digest('legacy-token')))
       self.assertIsNone(old.created_by_user_id)
       self.assertIsNone(old.used_at)
       self.assertEqual(db.get(User,sid).email,'reset-supplier@test.local')
      self.assertEqual(client.post(f'/admin/fornecedores/{sid}/reset-senha').status_code,403)
      client.post('/login',data={'email':'reset-admin@test.local','password':'AdminTest123!'})
      def generate():
       r=client.post(f'/admin/fornecedores/{sid}/reset-senha'); self.assertEqual(r.status_code,200)
       link=re.search(r'id="reset-link" value="([^"]+)"',r.text).group(1)
       self.assertTrue(link.startswith('https://cotacao.example/redefinir-senha/'))
       return '/redefinir-senha/'+link.rsplit('/',1)[1]
      first=generate(); second=generate()
      self.assertEqual(client.get(first).status_code,400)
      self.assertEqual(client.get('/redefinir-senha/legacy-token').status_code,400)
      self.assertEqual(client.get(second).status_code,200)
      for password,confirm in [('short','short'),('NewPassword123!','Mismatch123!')]:
       self.assertEqual(client.post(second,data={'password':password,'password_confirm':confirm}).status_code,400)
      self.assertEqual(client.post(second,data={'password':'NewPassword123!','password_confirm':'NewPassword123!'}).status_code,200)
      self.assertEqual(client.get(second).status_code,400)
      self.assertEqual(client.post(second,data={'password':'AnotherPassword123!','password_confirm':'AnotherPassword123!'}).status_code,400)
      expired=generate()
      with sessions() as db:
       reset=db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash==main.token_digest(expired.rsplit('/',1)[1])))
       self.assertIsNotNone(reset.created_by_user_id)
       reset.expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
      self.assertEqual(client.get(expired).status_code,400)
      client.post('/logout')
      self.assertEqual(client.post('/login',data={'email':'reset-supplier@test.local','password':'OldPassword123!'}).status_code,400)
      login=client.post('/login',data={'email':'reset-supplier@test.local','password':'NewPassword123!'})
      self.assertEqual(login.status_code,200);self.assertEqual(login.url.path,'/fornecedor')
      self.assertEqual(client.post(f'/admin/fornecedores/{sid}/reset-senha').status_code,403)
   finally:
    main.app.dependency_overrides.pop(get_db,None)
    engine.dispose()
