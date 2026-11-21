from tools.xn_log import Analyzer, read_new

PATIENT = """2026-11-14T08:00:00.000 [1] [INFO] x [TO_AS] Reception [ENC]<SampleResult Version="1.0">
  <Sample SampleID="S1" SampleArrivalDateTime="20261114080000" Control="false">
    <Result TestCode="20301" DateTime="20261114081500" UniqueID="S1#01#20301#a#65.2#umol/L">
      <ResultPart Part="1" Unit="umol/L">65.2</ResultPart>
    </Result>
    <Result TestCode="20302" DateTime="20261114082000" UniqueID="S1#01#20302#a#5#mmol/L">
      <ResultPart Part="1" Unit="mmol/L">5</ResultPart>
    </Result>
  </Sample>
</SampleResult>
"""
PATIENT2 = PATIENT.replace('"S1', '"S2').replace("20261114080000", "20261114090000") \
    .replace("20261114081500", "20261114093000").replace("20261114082000", "20261114094000")
QC = """<SampleResult><Sample SampleID="PC TN1" ControlName="PC TN1" Control="true">
    <Result TestCode="10239" DateTime="20261114083339" UniqueID="45#01#10239#t#0.0321#ng/mL">
      <SymmetricTargetValue Target="0.0294" StandardDeviation="0.00265" />
      <ResultPart Part="1" Unit="ng/mL">{v}</ResultPart>
      <Result TestCode="10239" DateTime="20261114083339" UniqueID="45#01#10239#t#12098#COUNT">
        <SymmetricTargetValue Target="0.0294" StandardDeviation="0.00265" />
        <ResultPart Part="1" Unit="COUNT">12098</ResultPart>
      </Result>
    </Result>
</Sample></SampleResult>
"""
STATUS = '<InstrumentStatus><Instrument InstrumentState="3" InstrumentStateDescription="Stand by" /></InstrumentStatus>'
DAY = "2026-11-14"


def test_counts_tat_qc_status_and_duplicates():
    a = Analyzer()
    # log Roche ghi mỗi bản tin 2 lần -> không được đếm trùng; số đếm thô COUNT bị bỏ qua
    a.feed(PATIENT + PATIENT + PATIENT2 + QC.format(v="0.0321") + STATUS, DAY)
    p = a.payload(DAY)
    assert p == {"samples_today": 2, "last_result": "09:40", "tat_median_min": 30,
                 "iqc": "Đạt", "status": "standby"}


def test_qc_fail_and_other_day_ignored():
    a = Analyzer()
    a.feed(QC.format(v="0.05") + PATIENT, "2026-11-15")      # kết quả ngày 14 không tính cho ngày 15
    assert a.payload("2026-11-15") == {"samples_today": 0, "iqc": "Chưa chạy"}
    a = Analyzer()
    a.feed(QC.format(v="0.05"), DAY)                          # z ≈ 7.7
    assert a.payload(DAY)["iqc"] == "Không đạt"


def test_incremental_read_and_rotation(tmp_path):
    f = tmp_path / "800--3.log"
    text = (PATIENT + PATIENT2).encode()
    a, carry = Analyzer(), ""
    for i in range(0, len(text), 97):                         # file lớn dần, cắt giữa bản tin
        with open(f, "ab") as h:
            h.write(text[i:i + 97])
        carry = read_new(str(f), a, carry, DAY)
    assert a.payload(DAY)["samples_today"] == 2
    f.write_bytes(STATUS.encode())                            # log xoay vòng: file mới nhỏ hơn
    carry = read_new(str(f), a, carry, DAY)
    assert a.payload(DAY)["status"] == "standby" and a.payload(DAY)["samples_today"] == 2
