"""
══════════════════════════════════════════════════════
  SYSTEM MONITOR ADVANCED v2.0
  Real-time monitoring + Alert + Process Monitoring
══════════════════════════════════════════════════════

Fitur:
  ✓ Monitor CPU, RAM, Disk, Baterai, Network real-time
  ✓ Notifikasi desktop saat CPU/RAM/Suhu kritis
  ✓ Monitoring proses (top aplikasi terberat)
  ✓ Bar visual dengan warna indikator
  ✓ Auto-refresh setiap 2 detik

Install:
  pip install psutil plyer

Jalankan:
  python system_monitor.py

Shortcut:
  Ctrl+C = Keluar
══════════════════════════════════════════════════════
"""

import psutil
import platform
import time
import os
from datetime import datetime

# ============================================
# COBA IMPORT PLYER UNTUK NOTIFIKASI DESKTOP
# ============================================
try:
    from plyer import notification
    NOTIF_TERSEDIA = True
except ImportError:
    NOTIF_TERSEDIA = False
    print("⚠ Library 'plyer' belum terinstall.")
    print("  Notifikasi desktop tidak aktif.")
    print("  Install dengan: pip install plyer")
    print("  (Program tetap berjalan tanpa notifikasi desktop)\n")

# ============================================
# KONFIGURASI BATAS PERINGATAN
# ============================================
BATAS = {
    "cpu_persen": 80,         # Alert jika CPU > 80%
    "ram_persen": 85,         # Alert jika RAM > 85%
    "disk_persen": 90,        # Alert jika Disk > 90%
    "suhu_celcius": 80,       # Alert jika Suhu > 80°C
    "baterai_rendah": 20,     # Alert jika Baterai < 20%
}

# Interval minimum antar notifikasi (detik) agar tidak spam
INTERVAL_NOTIF = 60
notif_terakhir = {}

# Jumlah proses teratas yang ditampilkan
TOP_PROSES = 8

# Interval refresh (detik)
REFRESH_INTERVAL = 2


# ============================================
# FUNGSI UTILITAS
# ============================================
def bersihkan_layar():
    """Membersihkan layar terminal."""
    os.system("cls" if os.name == "nt" else "clear")


def format_bytes(byte, satuan="auto"):
    """Konversi bytes ke format yang mudah dibaca."""
    if satuan == "auto":
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if byte < 1024:
                return f"{byte:.1f} {unit}"
            byte /= 1024
        return f"{byte:.1f} PB"
    return f"{byte:.1f}"


def buat_bar(persen, panjang=30):
    """Membuat bar visual dengan warna."""
    terisi = int(panjang * persen / 100)
    kosong = panjang - terisi

    # Warna berdasarkan persentase
    if persen < 50:
        warna = "\033[92m"   # Hijau
    elif persen < 80:
        warna = "\033[93m"   # Kuning
    else:
        warna = "\033[91m"   # Merah

    reset = "\033[0m"
    bar = f"{warna}{'█' * terisi}{reset}{'░' * kosong}"
    return bar


def status_warna(persen, terbalik=False):
    """Mengembalikan teks berwarna berdasarkan persentase."""
    if terbalik:
        # Untuk baterai: rendah = merah, tinggi = hijau
        if persen > 50:
            return f"\033[92m{persen:.1f}%\033[0m"
        elif persen > 20:
            return f"\033[93m{persen:.1f}%\033[0m"
        else:
            return f"\033[91m{persen:.1f}%\033[0m"
    else:
        if persen < 50:
            return f"\033[92m{persen:.1f}%\033[0m"
        elif persen < 80:
            return f"\033[93m{persen:.1f}%\033[0m"
        else:
            return f"\033[91m{persen:.1f}%\033[0m"


def kirim_notifikasi(judul, pesan, tipe="warning"):
    """Mengirim notifikasi desktop."""
    sekarang = time.time()
    kunci = f"{judul}_{tipe}"

    # Cek interval agar tidak spam
    if kunci in notif_terakhir:
        if sekarang - notif_terakhir[kunci] < INTERVAL_NOTIF:
            return

    notif_terakhir[kunci] = sekarang

    if NOTIF_TERSEDIA:
        try:
            notification.notify(
                title=f"⚠ {judul}",
                message=pesan,
                app_name="System Monitor",
                timeout=5,
            )
        except Exception:
            pass

    # Selalu simpan ke log alert
    with open("monitor_alerts.log", "a", encoding="utf-8") as f:
        waktu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{waktu}] [{tipe.upper()}] {judul}: {pesan}\n")


# ============================================
# FUNGSI MONITORING
# ============================================
def info_cpu():
    """Mengambil informasi CPU."""
    cpu_persen = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count(logical=False) or "?"
    cpu_thread = psutil.cpu_count(logical=True) or "?"

    try:
        cpu_freq = psutil.cpu_freq()
        freq_str = f"{cpu_freq.current / 1000:.2f} GHz" if cpu_freq else "N/A"
    except Exception:
        freq_str = "N/A"

    # Suhu CPU (tidak semua OS mendukung)
    suhu_str = "N/A"
    suhu_val = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for nama, entries in temps.items():
                if entries:
                    suhu_val = entries[0].current
                    if suhu_val < 60:
                        suhu_str = f"\033[92m{suhu_val:.0f}°C ✓ Normal\033[0m"
                    elif suhu_val < 80:
                        suhu_str = f"\033[93m{suhu_val:.0f}°C △ Hangat\033[0m"
                    else:
                        suhu_str = f"\033[91m{suhu_val:.0f}°C ✗ PANAS!\033[0m"
                    break
    except Exception:
        pass

    # Alert CPU
    if cpu_persen > BATAS["cpu_persen"]:
        kirim_notifikasi(
            "CPU Tinggi!",
            f"Penggunaan CPU mencapai {cpu_persen:.1f}%! "
            f"Pertimbangkan untuk menutup aplikasi berat.",
            "cpu_high"
        )

    # Alert Suhu
    if suhu_val and suhu_val > BATAS["suhu_celcius"]:
        kirim_notifikasi(
            "Suhu CPU Tinggi!",
            f"Suhu CPU mencapai {suhu_val:.0f}°C! "
            f"Pastikan ventilasi laptop tidak terhalang.",
            "temp_high"
        )

    return {
        "persen": cpu_persen,
        "core": cpu_count,
        "thread": cpu_thread,
        "frekuensi": freq_str,
        "suhu": suhu_str,
    }


def info_ram():
    """Mengambil informasi RAM."""
    ram = psutil.virtual_memory()
    persen = ram.percent

    # Alert RAM
    if persen > BATAS["ram_persen"]:
        kirim_notifikasi(
            "RAM Hampir Penuh!",
            f"Penggunaan RAM mencapai {persen:.1f}%! "
            f"({format_bytes(ram.used)} / {format_bytes(ram.total)}). "
            f"Tutup aplikasi yang tidak digunakan.",
            "ram_high"
        )

    return {
        "persen": persen,
        "terpakai": format_bytes(ram.used),
        "total": format_bytes(ram.total),
        "tersedia": format_bytes(ram.available),
    }


def info_disk():
    """Mengambil informasi Disk."""
    partisi_list = []
    try:
        for partisi in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partisi.mountpoint)
                persen = usage.percent

                # Alert Disk
                if persen > BATAS["disk_persen"]:
                    kirim_notifikasi(
                        "Disk Hampir Penuh!",
                        f"Drive {partisi.mountpoint} sudah {persen:.1f}% terpakai! "
                        f"Hapus file yang tidak diperlukan.",
                        f"disk_high_{partisi.mountpoint}"
                    )

                partisi_list.append({
                    "mount": partisi.mountpoint,
                    "persen": persen,
                    "terpakai": format_bytes(usage.used),
                    "total": format_bytes(usage.total),
                    "bebas": format_bytes(usage.free),
                })
            except PermissionError:
                continue
    except Exception:
        pass

    return partisi_list


def info_baterai():
    """Mengambil informasi Baterai."""
    try:
        baterai = psutil.sensors_battery()
        if baterai is None:
            return None

        persen = baterai.percent
        charging = baterai.power_plugged

        if baterai.secsleft == psutil.POWER_TIME_UNLIMITED:
            sisa = "Mengisi daya..."
        elif baterai.secsleft == psutil.POWER_TIME_UNKNOWN:
            sisa = "Menghitung..."
        else:
            jam = baterai.secsleft // 3600
            menit = (baterai.secsleft % 3600) // 60
            sisa = f"~{jam} jam {menit} menit tersisa"

        status = "\033[92m⚡ Charging\033[0m" if charging else "\033[93m🔋 Battery\033[0m"

        # Alert Baterai Rendah
        if persen < BATAS["baterai_rendah"] and not charging:
            kirim_notifikasi(
                "Baterai Hampir Habis!",
                f"Baterai tinggal {persen:.0f}%! "
                f"Segera hubungkan charger.",
                "battery_low"
            )

        return {
            "persen": persen,
            "status": status,
            "sisa": sisa,
            "charging": charging,
        }
    except Exception:
        return None


def info_network():
    """Mengambil informasi Network."""
    try:
        net = psutil.net_io_counters()
        koneksi = psutil.net_if_addrs()

        # Cari nama WiFi / interface aktif
        interface_aktif = "Tidak terhubung"
        ip_address = "N/A"

        for nama, addrs in koneksi.items():
            for addr in addrs:
                if addr.family == 2 and not addr.address.startswith("127."):
                    interface_aktif = nama
                    ip_address = addr.address
                    break

        return {
            "interface": interface_aktif,
            "ip": ip_address,
            "kirim": format_bytes(net.bytes_sent),
            "terima": format_bytes(net.bytes_recv),
        }
    except Exception:
        return None


def info_proses():
    """Mengambil daftar proses teratas berdasarkan CPU dan RAM."""
    proses_list = []

    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info"]):
        try:
            info = proc.info
            if info["cpu_percent"] is not None and info["memory_percent"] is not None:
                proses_list.append({
                    "pid": info["pid"],
                    "nama": info["name"][:25],
                    "cpu": info["cpu_percent"],
                    "ram": info["memory_percent"],
                    "ram_mb": info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Sort berdasarkan CPU + RAM
    proses_list.sort(key=lambda x: x["cpu"] + x["ram"], reverse=True)
    return proses_list[:TOP_PROSES]


# ============================================
# FUNGSI TAMPILAN
# ============================================
def tampilkan_header():
    """Menampilkan header program."""
    waktu = datetime.now().strftime("%d %b %Y, %H:%M:%S")
    sistem = f"{platform.system()} {platform.release()}"

    print("\033[95m" + "═" * 56 + "\033[0m")
    print("\033[95m   SYSTEM MONITOR ADVANCED v2.0\033[0m")
    print(f"\033[90m   {sistem} | {waktu}\033[0m")
    print("\033[95m" + "═" * 56 + "\033[0m")


def tampilkan_cpu(data):
    """Menampilkan informasi CPU."""
    print(f"\n\033[96m  [ CPU ]\033[0m")
    print(f"    Penggunaan  : {status_warna(data['persen'])}")
    print(f"    {buat_bar(data['persen'])} {data['persen']:.1f}%")
    print(f"    Core        : {data['core']} core / {data['thread']} thread")
    print(f"    Frekuensi   : {data['frekuensi']}")
    print(f"    Suhu        : {data['suhu']}")


def tampilkan_ram(data):
    """Menampilkan informasi RAM."""
    print(f"\n\033[96m  [ RAM ]\033[0m")
    print(f"    Terpakai    : {data['terpakai']} / {data['total']} ({status_warna(data['persen'])})")
    print(f"    {buat_bar(data['persen'])} {data['persen']:.1f}%")
    print(f"    Tersedia    : {data['tersedia']}")


def tampilkan_disk(data_list):
    """Menampilkan informasi Disk."""
    print(f"\n\033[96m  [ DISK ]\033[0m")
    for d in data_list[:3]:  # Maksimal 3 partisi
        print(f"    {d['mount']:<10} : {d['terpakai']} / {d['total']} ({status_warna(d['persen'])})")
        print(f"    {buat_bar(d['persen'])} {d['persen']:.1f}%")


def tampilkan_baterai(data):
    """Menampilkan informasi Baterai."""
    print(f"\n\033[96m  [ BATERAI ]\033[0m")
    if data is None:
        print("    Tidak terdeteksi (mungkin PC desktop)")
        return
    print(f"    Level       : {status_warna(data['persen'], terbalik=True)}")
    print(f"    {buat_bar(data['persen'])} {data['persen']:.1f}%")
    print(f"    Status      : {data['status']}")
    print(f"    Estimasi    : {data['sisa']}")


def tampilkan_network(data):
    """Menampilkan informasi Network."""
    print(f"\n\033[96m  [ NETWORK ]\033[0m")
    if data is None:
        print("    Tidak tersedia")
        return
    print(f"    Interface   : {data['interface']}")
    print(f"    IP Address  : {data['ip']}")
    print(f"    Total Kirim : {data['kirim']}")
    print(f"    Total Terima: {data['terima']}")


def tampilkan_proses(proses_list):
    """Menampilkan top proses."""
    print(f"\n\033[96m  [ TOP PROSES — Paling Berat ]\033[0m")
    print(f"    {'No':<4}{'Nama':<27}{'CPU %':<9}{'RAM %':<9}{'RAM (MB)':<10}")
    print(f"    {'─' * 4}{'─' * 27}{'─' * 9}{'─' * 9}{'─' * 10}")

    for i, p in enumerate(proses_list, 1):
        # Warna berdasarkan beban
        if p["cpu"] > 50 or p["ram"] > 10:
            warna = "\033[91m"  # Merah
        elif p["cpu"] > 20 or p["ram"] > 5:
            warna = "\033[93m"  # Kuning
        else:
            warna = "\033[0m"   # Normal

        print(f"    {warna}{i:<4}{p['nama']:<27}{p['cpu']:<9.1f}{p['ram']:<9.1f}{p['ram_mb']:<10.1f}\033[0m")


def tampilkan_alert_status():
    """Menampilkan status konfigurasi alert."""
    print(f"\n\033[90m  ─────────────────────────────────────────────────────\033[0m")
    notif_status = "\033[92mAKTIF\033[0m" if NOTIF_TERSEDIA else "\033[93mLOG ONLY\033[0m"
    print(f"\033[90m  Alert: {notif_status} | "
          f"CPU>{BATAS['cpu_persen']}% | "
          f"RAM>{BATAS['ram_persen']}% | "
          f"Suhu>{BATAS['suhu_celcius']}°C | "
          f"Bat<{BATAS['baterai_rendah']}%\033[0m")
    print(f"\033[90m  Refresh: {REFRESH_INTERVAL}s | Log: monitor_alerts.log | Ctrl+C = Keluar\033[0m")


# ============================================
# MAIN LOOP
# ============================================
def main():
    """Fungsi utama program."""
    print("\033[95m")
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║       SYSTEM MONITOR ADVANCED v2.0           ║")
    print("  ║   Real-time + Alert + Process Monitoring     ║")
    print("  ╚══════════════════════════════════════════════╝")
    print("\033[0m")
    print("  Memulai monitoring...\n")

    # Panggil cpu_percent sekali dulu agar akurat
    psutil.cpu_percent(interval=0.5)
    # Panggil per-proses cpu_percent sekali dulu
    for proc in psutil.process_iter(["cpu_percent"]):
        try:
            proc.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    time.sleep(1)

    try:
        while True:
            bersihkan_layar()
            tampilkan_header()

            # Ambil semua data
            cpu = info_cpu()
            ram = info_ram()
            disk = info_disk()
            baterai = info_baterai()
            network = info_network()
            proses = info_proses()

            # Tampilkan semua
            tampilkan_cpu(cpu)
            tampilkan_ram(ram)
            tampilkan_disk(disk)
            tampilkan_baterai(baterai)
            tampilkan_network(network)
            tampilkan_proses(proses)
            tampilkan_alert_status()

            time.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n\033[95m  Program dihentikan. Terima kasih! 👋\033[0m\n")


if __name__ == "__main__":
    main()
