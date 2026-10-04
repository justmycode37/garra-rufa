import test from 'node:test';
import assert from 'node:assert/strict';
import { startBrowserDictation, type SpeechRecognitionLike } from '../src/lib/browser-voice';

class Recognition implements SpeechRecognitionLike {
  static current: Recognition;
  lang=''; continuous=false; interimResults=false; maxAlternatives=0;
  onstart: SpeechRecognitionLike['onstart']=null;
  onresult: SpeechRecognitionLike['onresult']=null;
  onerror: SpeechRecognitionLike['onerror']=null;
  onend: SpeechRecognitionLike['onend']=null;
  started=false; stopped=false; aborted=false;
  constructor() { Recognition.current=this; }
  start() { this.started=true; }
  stop() { this.stopped=true; }
  abort() { this.aborted=true; }
}
function session() {
  const started: boolean[]=[];
  const previews: string[]=[];
  const completed: {text:string;error?:string}[]=[];
  const control=startBrowserDictation(Recognition,'en-GB',{
    onStart:()=>started.push(true), onText:text=>previews.push(text),
    onEnd:(text,error)=>completed.push({text,error}),
  });
  return {control,recognition:Recognition.current,started,previews,completed};
}

test('browser dictation works without a service key and replaces cumulative interim results', () => {
  const {control,recognition,started,previews,completed}=session();
  try {
    assert.equal(recognition.started,true);
    assert.equal(recognition.lang,'en-GB');
    assert.equal(recognition.interimResults,true);
    recognition.onstart!();
    recognition.onresult!({results:[[{transcript:'Find Mar'}]]});
    recognition.onresult!({results:[[{transcript:'Find Marfan research.'}],[{transcript:' Show papers.'}]]});
    control.stop();
    recognition.onend!();
    assert.deepEqual(started,[true]);
    assert.deepEqual(previews,['Find Mar','Find Marfan research.  Show papers.']);
    assert.deepEqual(completed,[{text:'Find Marfan research.  Show papers.',error:undefined}]);
  } finally { control.cancel(); }
});

test('cancel releases recognition and late speech events cannot insert private text', () => {
  const {control,recognition,previews,completed}=session();
  const lateResult=recognition.onresult!, lateEnd=recognition.onend!;
  control.cancel();
  lateResult({results:[[{transcript:'Discarded speech'}]]}); lateEnd();
  assert.equal(recognition.aborted,true);
  assert.equal(recognition.onresult,null);
  assert.deepEqual(previews,[]); assert.deepEqual(completed,[]);
});

test('permission and browser-service failures finish once with actionable notices', () => {
  for(const code of ['not-allowed','service-not-allowed','network','audio-capture','no-speech']) {
    const {control,recognition,completed}=session();
    const lateEnd=recognition.onend!;
    recognition.onerror!({error:code}); lateEnd();
    assert.equal(completed.length,1);
    assert.ok(completed[0].error);
    assert.equal(recognition.aborted,true);
    if(code==='not-allowed') assert.match(completed[0].error!,/browser settings/);
    control.cancel();
  }
});

test('recognition failure preserves words already captured for explicit review', () => {
  const {control,recognition,completed}=session();
  recognition.onresult!({results:[[{transcript:'Already captured'}]]});
  recognition.onerror!({error:'network'});
  assert.equal(completed[0].text,'Already captured');
  assert.match(completed[0].error!,/could not connect/);
  control.cancel();
});

test('a browser that never starts cannot leave the microphone panel stuck', t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const {recognition,completed,control}=session();
  t.mock.timers.tick(30000);
  assert.equal(completed.length,1);
  assert.match(completed[0].error!,/Chrome or Safari/);
  assert.equal(recognition.aborted,true);
  control.cancel();
});

test('stopping still completes if the browser omits its final end event', t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const {control,recognition,completed}=session();
  recognition.onstart!();
  recognition.onresult!({results:[[{transcript:'A test question'}]]});
  control.stop();
  t.mock.timers.tick(3000);
  assert.deepEqual(completed,[{text:'A test question',error:undefined}]);
  assert.equal(recognition.aborted,true);
  control.cancel();
});

test('synchronous startup failure cancels its watchdog and detaches listeners', t => {
  t.mock.timers.enable({apis:['setTimeout']});
  class BrokenRecognition extends Recognition { start() { throw new Error('unsupported'); } }
  let callbacks=0;
  assert.throws(()=>startBrowserDictation(BrokenRecognition,'en',{
    onStart:()=>callbacks++,onText:()=>callbacks++,onEnd:()=>callbacks++,
  }));
  t.mock.timers.tick(60000);
  assert.equal(callbacks,0);
  assert.equal(Recognition.current.onend,null);
  assert.equal(Recognition.current.aborted,true);
});
