import pytest
from app.wedding_media import FFmpegRenderer, ProviderUnavailable
from app.config import Settings


def test_ass_text_cannot_inject_layout():
    from app.wedding_render_media import ass_text
    escaped=ass_text('我们{\\pos(0,0)}\\N\n爱你')
    assert '{' not in escaped and '}' not in escaped
    assert escaped.count('\\N')==1


def test_actual_validator_rejects_fake_mp4(tmp_path):
    from app.wedding_render_media import validate_video
    path=tmp_path/'fake.mp4'; path.write_bytes(b'not an mp4')
    with pytest.raises(ProviderUnavailable):
        validate_video(path,Settings(_env_file=None),duration_ms=1000,resolution=(1280,720),require_audio=False)

import os
import json
import hashlib
from pathlib import Path
from io import BytesIO
from PIL import Image,ImageDraw
from app.wedding_media import RenderInput


def media_config():
    if os.environ.get('WEDDING_ACTUAL_MEDIA')!='1': pytest.skip('Set WEDDING_ACTUAL_MEDIA=1 with FFmpeg/ffprobe installed')
    return Settings(_env_file=None,wedding_ffmpeg_path=os.environ.get('WEDDING_FFMPEG_PATH'),wedding_ffprobe_path=os.environ.get('WEDDING_FFPROBE_PATH'))


def frame(path,settings,seconds):
    from app.wedding_render_media import run,binary
    raw,_=run([binary(settings,'ffmpeg'),'-v','error','-ss',str(seconds),'-i',str(path),'-frames:v','1','-f','image2pipe','-c:v','png','-'])
    return Image.open(BytesIO(raw)).convert('RGB')


def test_actual_render_sizes_motion_cjk_audio_and_full_90_seconds(tmp_path):
    cfg=media_config()
    from app.wedding_render_media import validate_video,run,binary
    root=Path(__file__).resolve().parents[2]; out=root/'output'/'wedding-task4-media';out.mkdir(parents=True,exist_ok=True)
    # Entirely synthetic portrait-shaped input with colored boundary markers.
    image=Image.new('RGB',(480,800),'#203040');draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,479,799),outline='#00ff00',width=22)
    draw.rectangle((100,150,380,650),fill='#b04c71');draw.line((0,0,480,800),fill='yellow',width=6)
    buf=BytesIO();image.save(buf,format='PNG');photo=buf.getvalue()
    tone=tmp_path/'internal-test-tone.wav'
    run([binary(cfg,'ffmpeg'),'-v','error','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=90','-ac','2',str(tone)])
    music={'path':str(tone),'sha256':hashlib.sha256(tone.read_bytes()).hexdigest(),'license_id':'TEST-ONLY-NO-COMMERCIAL-GRANT','perpetual_embedded_delivery':True,'allowed_uses':['customer_embedded_delivery','wedding_playback']}
    reports={}
    for size in ((1280,720),(1920,1080)):
        renderer=FFmpegRenderer(cfg)
        caption='从相遇到相守\n一起走过每个春秋' if size[0]==1280 else '爱你{\\pos(0,0)}\\N\n一起走过每个春秋'
        output=renderer.render([RenderInput(photo,'image/png',caption,3000)],demo=True,resolution=size,music_snapshot=music)
        path=out/f'{size[0]}-short.mp4';path.write_bytes(output.data)
        info=validate_video(path,cfg,duration_ms=3000,resolution=size,require_audio=True,measure_audio=True)
        reports[str(size)]=info
        one=frame(path,cfg,0.2);two=frame(path,cfg,2.7);one.save(out/f'{size[0]}-frame.png')
        import numpy as np
        a=np.asarray(one);b=np.asarray(two)
        assert np.abs(a.astype(float)-b.astype(float)).mean()>1,'Photo must visibly move'
        # Green source borders at both top/bottom remain inside the padded frame.
        for pixels in (a,b):
            green=(pixels[:,:,1]>150)&(pixels[:,:,0]<80)&(pixels[:,:,2]<100)
            ys,xs=np.where(green);assert ys.min()>0 and ys.max()<size[1]-1 and xs.min()>0 and xs.max()<size[0]-1
        bottom=a[int(size[1]*.75):];white=(bottom.min(axis=2)>220)
        ys,xs=np.where(white);assert len(xs)>100 and xs.min()>50 and xs.max()<size[0]-50
        # Truncated file must fail full probe/decode, not become ready.
        damaged=tmp_path/'truncated.mp4';damaged.write_bytes(output.data[:len(output.data)//2])
        with pytest.raises(ProviderUnavailable):validate_video(damaged,cfg,duration_ms=3000,resolution=size,require_audio=True)
    # Provider clip deliberately includes a different 1500 Hz audio track.
    provider=tmp_path/'provider.mp4'
    run([binary(cfg,'ffmpeg'),'-v','error','-y','-f','lavfi','-i','color=c=navy:s=1280x720:r=30:d=3','-f','lavfi','-i','sine=frequency=1500:duration=3:sample_rate=48000','-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(provider)])
    output=FFmpegRenderer(cfg).render([RenderInput(provider.read_bytes(),'video/mp4','只使用授权音乐',3000)],demo=True,resolution=(1280,720),music_snapshot=music)
    path=out/'provider-audio-stripped.mp4';path.write_bytes(output.data)
    raw,_=run([binary(cfg,'ffmpeg'),'-v','error','-i',str(path),'-vn','-ac','1','-ar','48000','-f','f32le','-'])
    import numpy as np
    values=np.frombuffer(raw,dtype='<f4');spectrum=np.abs(np.fft.rfft(values));freq=np.fft.rfftfreq(len(values),1/48000)
    assert abs(freq[spectrum.argmax()]-440)<2
    assert spectrum[np.argmin(abs(freq-1500))]<spectrum.max()*.01
    inputs=[RenderInput(photo,'image/png',f'第{i+1}幕 一起走过春秋',5000) for i in range(18)]
    output=FFmpegRenderer(cfg).render(inputs,demo=True,resolution=(1280,720),music_snapshot=music)
    path=out/'90-second-18-shot-preview.mp4';path.write_bytes(output.data)
    reports['90s']=validate_video(path,cfg,duration_ms=90000,resolution=(1280,720),require_audio=True,measure_audio=True)
    (out/'actual-results.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')


def test_actual_widest_allowed_subtitle_stays_within_safe_margins(tmp_path):
    cfg=media_config()
    from PIL import Image
    import numpy as np
    source=BytesIO();Image.new('RGB',(1024,1024),'black').save(source,format='PNG')
    output=FFmpegRenderer(cfg).render([RenderInput(source.getvalue(),'image/png','W'*36+'\n'+'中'*18,1000)],demo=True,resolution=(1280,720))
    path=tmp_path/'widest.mp4';path.write_bytes(output.data)
    pixels=np.asarray(frame(path,cfg,.5));lower=pixels[360:]
    y,x=np.where(lower.min(axis=2)>220)
    assert len(x)>100 and x.min()>=80 and x.max()<=1200
