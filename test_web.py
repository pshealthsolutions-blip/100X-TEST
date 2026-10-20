import os, unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as web

class WebTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ, {'GROQ_API_KEY':'offline-test-key','ALL_PROXY':'','HTTPS_PROXY':'','HTTP_PROXY':'','all_proxy':'','https_proxy':'','http_proxy':''}); self.env.start(); self.api=TestClient(web.app)
 def tearDown(self): self.env.stop()
 def send(self,m,t=''): return self.api.post('/api/chat',json={'message':m,'token':t})
 def test_learning_flow(self):
  self.assertIn('Learning Assistant Chatbot',self.api.get('/').text)
  self.assertIn('Choose a lesson',self.api.get('/api/welcome').json()['reply'])
  r=self.send('1').json(); self.assertIn('Question 1 of 3',r['reply']); t=r['token']
  self.assertIn('Hint:',self.send('Hint',t).json()['reply'])
  self.assertIn('Try this question first',self.send('Next',t).json()['reply'])
  grade=Obj(choices=[Obj(message=Obj(content='{"passed":true,"feedback":"Correct reasoning."}'))])
  with patch('groq.resources.chat.completions.Completions.create',return_value=grade): r=self.send('Generated does not prove delivery',t).json()
  self.assertIn('Understanding checked',r['reply'])
  self.assertIn('Question 2 of 3',self.send('Next',r['token']).json()['reply'])
  self.assertEqual(self.send('Next',t+'tampered').status_code,400)
 def test_errors(self):
  self.assertEqual(self.send(' ').status_code,422)
  self.assertEqual(self.send('x'*6001).status_code,422)
  t=self.send('1').json()['token']
  with patch('groq.resources.chat.completions.Completions.create',side_effect=RuntimeError('private error')):
   r=self.send('reasoning',t)
  self.assertEqual(r.status_code,502); self.assertNotIn('private error',r.text)
  self.assertIn('Question 1 of 3',self.send('Explain',t).json()['reply'])
  with patch.dict(os.environ,{'GROQ_API_KEY':''}):
   self.assertEqual(self.api.get('/').status_code,200)
   self.assertEqual(self.send('1').status_code,503)
 def test_audio(self):
  r=self.api.post('/api/transcribe',files={'file':('x.txt',b'text','text/plain')}); self.assertEqual(r.status_code,415)
  r=self.api.post('/api/transcribe',files={'file':('x.webm',b'0'*3000001,'audio/webm')}); self.assertEqual(r.status_code,413)
  with patch('groq.resources.audio.transcriptions.Transcriptions.create',return_value=Obj(text='Test dictation')):
   r=self.api.post('/api/transcribe',files={'file':('x.webm',b'audio','audio/webm')})
  self.assertEqual(r.json()['text'],'Test dictation')

if __name__=='__main__': unittest.main()
