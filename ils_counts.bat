@echo off
cd /d C:\Scripts\counts\

"C:\ProgramData\Miniconda3\condabin\conda.bat" run -n counts python "C:\Scripts\counts\ils_counts.py" >> task_log.txt 2>&1
