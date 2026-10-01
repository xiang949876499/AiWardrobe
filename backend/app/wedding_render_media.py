"""Bounded sequential FFmpeg rendering and independent decoded-media validation."""
from pathlib import Path
from fractions import Fraction
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time

from app.config import get_settings
from app.wedding_media import ProviderUnavailable, RenderedVideo

MAX_OUTPUT = 256 * 1024 ** 2
MAX_INPUT = 512 * 1024 ** 2


def binary(settings, name):
    configured=getattr(settings,'wedding_'+name+'_path')
    if configured: return configured
    if name=='ffprobe' and settings.wedding_ffmpeg_path:
        sibling=Path(settings.wedding_ffmpeg_path).with_name('ffprobe'+Path(settings.wedding_ffmpeg_path).suffix)
        if sibling.is_file(): return str(sibling)
    return shutil.which(name)


def run(args, *, cwd=None, timeout=120, heartbeat=None):
    # File-backed logs prevent unbounded subprocess PIPE allocations; loglevel error
    # limits media diagnostics. Public failures never disclose paths or media content.
    if not args[0]: raise ProviderUnavailable('FFMPEG_UNAVAILABLE','Media tools unavailable')
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process=subprocess.Popen(args,cwd=cwd,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,shell=False)
            deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    if heartbeat: heartbeat()
                    if time.monotonic()>deadline or stdout.tell()>1024**2 or stderr.tell()>1024**2:
                        raise ProviderUnavailable('FFMPEG_TIMEOUT','Media resource limit exceeded')
                    time.sleep(0.1)
            finally:
                if process.poll() is None: process.kill(); process.wait()
            if process.returncode:
                raise ProviderUnavailable('MEDIA_INVALID','Media cannot be decoded')
            stdout.seek(0);stderr.seek(0)
            return stdout.read(1024**2),stderr.read(1024**2)
        except OSError:
            raise ProviderUnavailable('FFMPEG_UNAVAILABLE','Media tools unavailable') from None


def validate_video(path,settings=None,*,duration_ms,resolution=None,require_audio=False,heartbeat=None,measure_audio=False):
    settings=settings or get_settings()
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_OUTPUT:
        raise ProviderUnavailable('MEDIA_INVALID','Invalid media size')
    raw,_=run([binary(settings,'ffprobe'),'-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format','-of','json',str(path)],heartbeat=heartbeat)
    try:
        info=json.loads(raw);streams=info['streams'];videos=[s for s in streams if s['codec_type']=='video'];audios=[s for s in streams if s['codec_type']=='audio']
        v=videos[0];duration=float(info['format']['duration'])
        valid=len(videos)==1 and v['codec_name']=='h264' and v['pix_fmt']=='yuv420p' and abs(duration-duration_ms/1000)<=0.12
        if resolution:
            valid=valid and (v['width'],v['height'])==resolution and Fraction(v['avg_frame_rate'])==30 and abs(float(v['duration'])-duration_ms/1000)<=0.08 and abs(int(v['nb_frames'])-round(duration_ms*30/1000))<=1
        else:
            valid=valid and 0<v['width']<=4096 and 0<v['height']<=4096
        if require_audio:
            valid=valid and len(audios)==1 and audios[0]['codec_name']=='aac' and audios[0]['channels']==2 and int(audios[0]['sample_rate'])==48000
        if not valid or not math.isfinite(duration): raise ValueError()
    except (ValueError,KeyError,IndexError,TypeError,ZeroDivisionError):
        raise ProviderUnavailable('MEDIA_INVALID','Unexpected media streams or duration') from None
    run([binary(settings,'ffmpeg'),'-v','error','-xerror','-err_detect','explode','-protocol_whitelist','file,pipe','-threads',str(settings.wedding_render_threads),'-i',str(path),'-map','0:v:0','-map','0:a?','-f','null','-'],timeout=settings.wedding_render_timeout_seconds,heartbeat=heartbeat)
    if measure_audio and require_audio:
        _,log=run([binary(settings,'ffmpeg'),'-hide_banner','-nostats','-protocol_whitelist','file,pipe','-threads','2','-i',str(path),'-vn','-af','loudnorm=I=-16:TP=-1:print_format=json','-f','null','-'],heartbeat=heartbeat)
        try:
            text=log.decode();stats=json.JSONDecoder().raw_decode(text[text.rfind('{'):])[0];lufs=float(stats['input_i']);peak=float(stats['input_tp'])
            if not math.isfinite(lufs) or abs(lufs+16)>1 or peak > -1: raise ValueError()
            info['audio_measurement']={'integrated_lufs':lufs,'true_peak_dbtp':peak}
        except (ValueError,KeyError):
            raise ProviderUnavailable('AUDIO_LEVEL_INVALID','Decoded audio is outside delivery levels') from None
    return info


def ass_text(text):
    from app.wedding_authoring import validate_subtitle
    # Fullwidth literal punctuation avoids libass override and escape interpretation.
    return validate_subtitle(text).replace('\\','∖').replace('{','[').replace('}',']').replace('\n',r'\N')


def ass_timestamp(ms):
    seconds,cs=divmod(ms//10,100);minutes,seconds=divmod(seconds,60);hours,minutes=divmod(minutes,60)
    return f'{hours}:{minutes:02}:{seconds:02}.{cs:02}'


def subtitles(inputs,width,height,font,demo):
    size=round(height*0.058);margin=round(height*0.065)
    # Font is operator configuration, never user text. No ASS delimiter/control injection.
    font=''.join(c for c in font if c.isalnum() or c in ' -_')[:80] or 'sans-serif'
    result=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,2,0,2,{margin*2},{margin*2},{margin},1
Style: Demo,{font},{size//2},&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,3,2,0,7,30,30,25,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    cursor=0
    for item in inputs:
        longest=max((len(line) for line in item.subtitle.split('\n')),default=1)
        fitted=min(size,int((width-4*margin)/(max(1,longest)*1.1)))
        override='{'+chr(92)+'fs'+str(fitted)+'}'
        result+=f'Dialogue: 0,{ass_timestamp(cursor)},{ass_timestamp(cursor+item.duration_ms)},Default,,0,0,0,,{override}{ass_text(item.subtitle)}\n'
        cursor+=item.duration_ms
    if demo: result+=f'Dialogue: 1,0:00:00.00,{ass_timestamp(cursor)},Demo,,0,0,0,,DEMO ONLY - NO AI MOTION\n'
    return result


class FFmpegRenderer:
    def __init__(self,settings=None,*,ffmpeg_path=None,timeout_seconds=None):
        self.settings=settings or get_settings()
        self.ffmpeg_path=ffmpeg_path or binary(self.settings,'ffmpeg')
        self.timeout_seconds=timeout_seconds or self.settings.wedding_render_timeout_seconds

    def validate_music_license(self,*,demo,music_snapshot=None):
        if demo and not music_snapshot:return None
        from app.wedding_authoring import historical_music
        from fastapi import HTTPException
        try:
            snapshot=historical_music(music_snapshot or {})
            if not snapshot: raise HTTPException(409,'MUSIC_LICENSE_REQUIRED')
            return Path(snapshot['path'])
        except HTTPException as exc:
            raise ProviderUnavailable(str(exc.detail),'An explicit valid license snapshot is required') from None

    def render(self,inputs,*,demo,resolution,music_snapshot=None,heartbeat=None):
        if not self.ffmpeg_path:raise ProviderUnavailable('FFMPEG_UNAVAILABLE','Media tools unavailable')
        total=sum(i.duration_ms for i in inputs)
        if not inputs or len(inputs)>18 or not 0<total<=90000 or any(i.duration_ms<=0 or i.duration_ms%1000 for i in inputs) or sum(len(i.data) for i in inputs)>MAX_INPUT or resolution not in ((1280,720),(1920,1080)):
            raise ProviderUnavailable('TIMELINE_INVALID','Timeline exceeds render limits')
        music_path=self.validate_music_license(demo=demo,music_snapshot=music_snapshot)
        music=None
        if music_path:
            if music_path.stat().st_size>MAX_INPUT:raise ProviderUnavailable('MUSIC_FILE_CHANGED','Music exceeds resource limit')
            music=music_path.read_bytes()
            if hashlib.sha256(music).hexdigest()!=music_snapshot.get('sha256'):raise ProviderUnavailable('MUSIC_FILE_CHANGED','Music bytes changed')
        deadline=time.monotonic()+self.timeout_seconds
        def bounded_heartbeat():
            if heartbeat:heartbeat()
            if time.monotonic()>deadline:raise ProviderUnavailable('FFMPEG_TIMEOUT','Render deadline exceeded')
        def execute(args,workdir):
            return run(args,cwd=workdir,timeout=max(1,deadline-time.monotonic()),heartbeat=bounded_heartbeat)
        base=[self.ffmpeg_path,'-hide_banner','-loglevel','error','-nostdin','-y','-max_alloc','67108864','-protocol_whitelist','file,pipe','-threads',str(self.settings.wedding_render_threads),'-filter_threads','1','-filter_complex_threads','1']
        w,h=resolution
        with tempfile.TemporaryDirectory(prefix='wedding-render-') as temp:
            work=Path(temp)
            for n,item in enumerate(inputs):
                ext={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','video/mp4':'.mp4','video/webm':'.webm'}.get(item.mime)
                if ext is None:raise ProviderUnavailable('RENDER_INPUT_UNSUPPORTED','Unsupported timeline media')
                if item.mime.startswith('image/'):
                    from PIL import Image
                    from io import BytesIO
                    with Image.open(BytesIO(item.data)) as picture:
                        if picture.width*picture.height>24_000_000 or max(picture.size)>10000:
                            raise ProviderUnavailable('RENDER_INPUT_LIMIT','Image decode exceeds pixel limit')
                name=f'input-{n}{ext}';(work/name).write_bytes(item.data)
                duration=f'{item.duration_ms/1000:.3f}';frames=item.duration_ms*30//1000
                args=base.copy()
                if item.mime.startswith('image/'):
                    # Fit source in 90% canvas: <=4% zoom and <=1% pan can never crop source.
                    args+=['-i',name]
                    vf=f"scale={int(w*.9)//2*2}:{int(h*.9)//2*2}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,zoompan=z='1+0.04*on/{max(1,frames-1)}':x='iw/2-iw/zoom/2+0.005*iw*sin(on/{frames}*PI)':y='ih/2-ih/zoom/2':d={frames}:s={w}x{h}:fps=30"
                else:
                    args+=['-i',name]
                    vf=f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30'
                vf+=f',trim=duration={duration},setpts=PTS-STARTPTS,setsar=1,format=yuv420p'
                args+=['-vf',vf,'-map','0:v:0','-an','-frames:v',str(frames),'-c:v','libx264','-threads',str(self.settings.wedding_render_threads),'-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-fs',str(MAX_OUTPUT),f'clip-{n}.mp4']
                execute(args,work)
                (work/name).unlink()
                if sum(p.stat().st_size for p in work.iterdir())>768*1024**2:
                    raise ProviderUnavailable('RENDER_SCRATCH_LIMIT','Render scratch capacity exceeded')
            (work/'clips.txt').write_text(''.join(f"file 'clip-{n}.mp4'\n" for n in range(len(inputs))))
            (work/'captions.ass').write_text(subtitles(inputs,w,h,self.settings.wedding_subtitle_font,demo),encoding='utf-8')
            args=base+['-f','concat','-safe','1','-i','clips.txt']
            if music_path:
                if music_path.stat().st_size>MAX_INPUT:raise ProviderUnavailable('MUSIC_FILE_CHANGED','Music exceeds resource limit')
                music=music_path.read_bytes()
                if hashlib.sha256(music).hexdigest()!=music_snapshot.get('sha256'):raise ProviderUnavailable('MUSIC_FILE_CHANGED','Music bytes changed')
                (work/'music').write_bytes(music)
                args+=['-protocol_whitelist','file,pipe','-stream_loop','-1','-i','music','-map','0:v:0','-map','1:a:0','-af',f'atrim=duration={total/1000},asetpts=PTS-STARTPTS,loudnorm=I=-16:LRA=11:TP=-2','-c:a','aac','-b:a','192k','-ac','2','-ar','48000']
            else: args+=['-map','0:v:0','-an']
            args+=['-vf','ass=captions.ass','-t',str(total/1000),'-c:v','libx264','-threads',str(self.settings.wedding_render_threads),'-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-r','30','-metadata','comment=AI-assisted wedding video; human identity review required'+('; DEMO ONLY' if demo else ''),'-movflags','+faststart','-fs',str(MAX_OUTPUT),'render.mp4']
            execute(args,work)
            cfg=self.settings.model_copy(update={'wedding_ffmpeg_path':self.ffmpeg_path})
            validate_video(work/'render.mp4',cfg,duration_ms=total,resolution=resolution,require_audio=bool(music_path),measure_audio=bool(music_path),heartbeat=bounded_heartbeat)
            return RenderedVideo((work/'render.mp4').read_bytes(),total,demo)
