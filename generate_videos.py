# -*- coding: utf-8 -*-
import os
import sys
import shutil
import random
import subprocess
import time
import tempfile
from datetime import datetime
from pathlib import Path


class VideoGenerator:
    """视频生成器核心类"""
    
    def __init__(self, video_dir, audio_dir, output_dir, num_videos=3):
        # 初始化路径
        self.video_dir = Path(video_dir).resolve()
        self.audio_dir = Path(audio_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.num_videos = num_videos
        
        # 初始化日志
        self.log = {
            'start_time': time.time(),
            'success': 0,
            'failures': [],
            'warnings': [],
            'used_clips': set(),
            'temp_files': set()
        }
        
        # 验证目录
        self._validate_directories()

    def _validate_directories(self):
        """验证输入输出目录"""
        required_dirs = {
            '视频目录': self.video_dir,
            '音频目录': self.audio_dir,
            '输出目录': self.output_dir
        }
        
        for name, path in required_dirs.items():
            if not path.exists():
                raise FileNotFoundError(f"{name}不存在: {path}")
            if name == '输出目录':
                path.mkdir(exist_ok=True)

    def _get_media_duration(self, file_path):
        """获取媒体文件时长（带重试机制）"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True
                )
                return float(result.stdout)
            except subprocess.CalledProcessError as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"获取时长失败: {e.stderr}")
                time.sleep(0.5)

    def _clean_output(self):
        """安全清空输出目录"""
        deleted = 0
        for item in self.output_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    deleted += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    deleted += 1
            except Exception as e:
                self.log['warnings'].append(f"删除失败 {item.name}: {str(e)}")
        return deleted

    def _generate_single(self, audio_path, task_num):
        """生成单个视频"""
        start_time = time.time()
        temp_dir = tempfile.TemporaryDirectory(prefix=f"video_{task_num}_")
        self.log['temp_files'].add(temp_dir.name)
        
        try:
            # 步骤1：获取音频时长
            audio_duration = self._get_media_duration(audio_path)
            print(f"\n▶️  开始任务 {task_num} | 目标时长: {audio_duration:.2f}s")
            print(f"📁 临时目录: {temp_dir.name}")
            
            # 步骤2：选择视频片段
            video_files = []
            for f in self.video_dir.glob('*'):
                if f.suffix.lower() in ('.mp4', '.mov', '.avi', '.mkv'):
                    try:
                        dur = self._get_media_duration(f)
                        video_files.append((f, dur))
                    except Exception as e:
                        self.log['warnings'].append(f"跳过 {f.name}: {str(e)}")
            
            if not video_files:
                raise RuntimeError("没有可用视频文件")
            
            random.shuffle(video_files)
            selected = []
            total_dur = 0.0
            
            # 步骤3：动态选择片段
            for idx, (video, dur) in enumerate(video_files):
                if total_dur >= audio_duration:
                    break
                
                if total_dur + dur <= audio_duration:
                    selected.append(video)
                    total_dur += dur
                    print(f"   ├─ 添加 {video.name} ({dur:.2f}s) [累计: {total_dur:.2f}s]")
                    self.log['used_clips'].add(video.name)
                else:
                    cut_duration = audio_duration - total_dur
                    selected.append((video, cut_duration))
                    print(f"   ├─ ✂️  裁剪 {video.name} ({dur:.2f}s → {cut_duration:.2f}s)")
                    total_dur = audio_duration
                    break
            
            # 步骤4：生成拼接列表
            concat_list = Path(temp_dir.name) / "concat.txt"
            with open(concat_list, 'w', encoding='utf-8') as f:
                for item in selected:
                    if isinstance(item, tuple):
                        f.write(f"file '{item[0].resolve()}'\n")
                        f.write(f"duration {item[1]}\n")
                    else:
                        f.write(f"file '{item.resolve()}'\n")
            
            # 步骤5：拼接视频
            temp_video = Path(temp_dir.name) / "temp.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list), "-c", "copy", str(temp_video)
            ], check=True, stderr=subprocess.DEVNULL)
            
            # 步骤6：合并音视频
            output_path = self.output_dir / f"output_{task_num}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(temp_video), "-i", str(audio_path),
                "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", str(output_path)
            ], check=True, stderr=subprocess.DEVNULL)
            
            # 记录成功
            self.log['success'] += 1
            print(f"✅ 任务 {task_num} 成功 | 用时: {time.time()-start_time:.2f}s")
            
        except Exception as e:
            self.log['failures'].append({
                'task': task_num,
                'reason': str(e),
                'temp_dir': temp_dir.name
            })
            print(f"❌ 任务 {task_num} 失败: {str(e)}")
            
        finally:
            temp_dir.cleanup()
            print(f"🔄 清理临时文件: {temp_dir.name}")

    def generate_all(self):
        """执行完整生成流程"""
        print("="*50)
        print(f"🎬 视频生成系统启动 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 清理输出目录
        print("\n🧹 正在清理输出目录...")
        deleted = self._clean_output()
        print(f"   └─ 已删除 {deleted} 个旧文件")
        
        # 选择音频文件
        audio_files = list(self.audio_dir.glob("*.mp3"))
        if not audio_files:
            raise FileNotFoundError("未找到音频文件")
        audio_path = random.choice(audio_files)
        print(f"\n🎵 使用音频: {audio_path.name}")
        
        # 执行生成任务
        print("\n🚀 开始生成视频:")
        for i in range(1, self.num_videos+1):
            self._generate_single(audio_path, i)
        
        # 生成报告
        print("\n")
        print("="*50)
        print("📈 生成报告:")
        print(f"✅ 成功任务: {self.log['success']}/{self.num_videos}")
        print(f"❌ 失败任务: {len(self.log['failures'])}")
        
        if self.log['warnings']:
            print("\n⚠️ 警告列表:")
            for warn in set(self.log['warnings']):
                count = self.log['warnings'].count(warn)
                print(f" - {warn} ({count}次)")
        
        print(f"\n💽 资源统计:")
        print(f" - 使用视频片段: {len(self.log['used_clips'])}个")
        print(f" - 总耗时: {time.time()-self.log['start_time']:.2f}秒")
        print("="*50)

if __name__ == "__main__":
    try:
        # 配置参数
        generator = VideoGenerator(
            video_dir="video",
            audio_dir="mp3",
            output_dir="output",
            num_videos=5
        )
        generator.generate_all()
    except Exception as e:
        print(f"\n❌ 致命错误: {str(e)}")
        sys.exit(1)