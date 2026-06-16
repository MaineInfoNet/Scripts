@echo off
cd /d C:\Scripts\innreach\innreach_counts\

"C:\ProgramData\Miniconda3\condabin\conda.bat" run -n counts python "C:\Scripts\innreach\innreach_counts\innreach_counts.py" >> task_log.txt 2>&1
