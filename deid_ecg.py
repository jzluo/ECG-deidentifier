import argparse
import csv
import datetime as dt
import os
import re
import sys
import xml.etree.ElementTree as et

from collections import defaultdict
from dateutil import parser, relativedelta
from subprocess import run, CalledProcessError, DEVNULL
from tqdm import tqdm


def resource_path(relative_path):
    """    Get absolute path to resource for PyInstaller's --onefile temp dir.
           https://stackoverflow.com/a/44352931/11151077

    Parameters
    ----------
    relative_path : str

    Returns
    -------
    str
        Absolute path of the resource.

    """
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def PDFtoSVG(phi_ecg, out_dir):
    """Calls mutool to convert a PDF to an SVG

    Parameters
    ----------
    phi_ecg: str
        Path to PDF input file
    out_dir: str

    Returns
    -------
    phi_svg: str
        Path of generated SVG

    """
    phi_svg = os.path.join(out_dir, '{}.svg'.format(os.path.basename(phi_ecg).split('.')[0]))
    mutool = [resource_path('mutool.exe'), 'convert', '-F', 'svg', '-O', 'text=text', '-o', phi_svg, phi_ecg]
    run(args=mutool, shell=True, check=True, stderr=DEVNULL)
    return phi_svg


def deidentify(mrn, phi_ecg, ecg_key, id_key, out_dir):
    """Converts a PDF of an ECG with PHI to a de-identified SVG

    Parameters
    ----------
    phi_ecg: str
        Path to the PHI-ECG PDF
    ecg_key: dict
        Nested dict of {'PHI_ID':{'PHI_ECG_DATE':'DEID_ECGG_DATE'}}
    id_key: dict
        Nested dict of {'PHI_ID':{'DEID_ID':'DEID_BDAY'}}
    out_dir: str
        Output directory path

    """
    try:
        phi_svg = PDFtoSVG(phi_ecg, out_dir)

    except CalledProcessError as e:
        with open('error_log.txt', 'a') as log:
            log.write('{}  Error converting {}: {}\n'.format(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                             os.path.basename(phi_ecg),
                                                             e.output)
            )
        return

    ns = {'svg': 'http://www.w3.org/2000/svg'}
    xmlparser = et.XMLParser(encoding='UTF-8')
    tree = et.parse('{}1.svg'.format(phi_svg.split('.')[0]), parser=xmlparser)
    root = tree.getroot()

    text_elements = root.findall('.//svg:tspan', ns)

    # mrn = text_elements[16].text.split(':')[1].lstrip('0')
    # mrn = os.path.basename(phi_ecg).split('_')[0]

    ele_idx = {'mrn': 'ID:',
               'ghs': 'Geisinger Health System',
               '25mm/s': '25mm/s',
               'refby': 'Referred by:',
               'confby': 'Confirmed By:',
               'prtaxes': 'P-R-T axes',
               'technician': 'Technician:'
    }
    match_terms = [term for term in ele_idx]
    texts = [x.text for x in text_elements]

    for i, text in enumerate(texts):
        for term in match_terms:
            if re.match(ele_idx[term], text):
                ele_idx[term] = i
                match_terms.remove(term)
                break

    ele_idx['name'] = ele_idx['mrn'] - 1
    ele_idx['ecg_date'] = ele_idx['mrn'] + 1
    ele_idx['finding_start'] = ele_idx['ghs'] + 1
    ele_idx['finding_end'] = ele_idx['25mm/s']
    ele_idx['bday'] = ele_idx['prtaxes'] + 1

    try:
        ecg_date = parser.parse(text_elements[ele_idx['ecg_date']].text)

    except ValueError:
        with open('error_log.txt', 'a') as log:
            log.write(
                '{}   ECG {}: wrong field for IMG_DT: {}\n'.format(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                                   phi_ecg,
                                                                   text_elements[ele_idx['ecg_date']].text)
            )
        return

    try:
        pt_id = list(id_key[mrn])[0]

    except IndexError:
        with open('error_log.txt', 'a') as log:
            log.write(
                '{}   MRN {} not present in ID key\n'.format(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), mrn)
            )
        return

    try:
        deid_ecg_date = ecg_key[mrn][ecg_date]

    except KeyError:
        with open('error_log.txt', 'a') as log:
            log.write('{}   MRN {}: ECG date {} is not present in ECG key\n'.format(
                dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                mrn,
                str(ecg_date))
            )
        return

    strf_ecg = '%d-%b-%Y %H:%M:%S'
    strf_ecg_alt = '%d-%b-%Y %H:%M'

    text_elements[ele_idx['name']].text = pt_id  # replace name with PT_ID
    text_elements[ele_idx['name']].attrib['x'] = text_elements[ele_idx['name']].attrib['x'].split()[0]  # remove old per-glyph x-coords

    text_elements[ele_idx['ecg_date']].text = deid_ecg_date.strftime(strf_ecg)  # replace ECG date
    text_elements[ele_idx['ecg_date']].attrib['x'] = text_elements[ele_idx['ecg_date']].attrib['x'].split()[0]

    text_elements[ele_idx['bday']].text = '{} ({} yr)'.format(
        dt.datetime.strftime(id_key.get(mrn).get(pt_id), '%d-%b-%Y').upper(),
        relativedelta.relativedelta(ecg_key.get(mrn).get(ecg_date), id_key.get(mrn).get(pt_id)).years
    )

    text_elements[-1].clear()  # remove EID EDT ORDER ACCOUNT field
    text_elements[ele_idx['mrn']].clear()  # remove ID field

    try:
        text_elements[ele_idx['technician']].text = 'Technician:'
    except TypeError:
        with open('error_log.txt', 'a') as log:
            log.write('{}   Please verify that {} has no "Technician:" field\n'.format(
                dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                phi_ecg)
            )

    try:
        text_elements[ele_idx['confby']].text = 'Confirmed By:'
    except TypeError:
        with open('error_log.txt', 'a') as log:
            log.write('{}   Please verify that {} has no "Confirmed by:" field\n'.format(
                dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                phi_ecg)
            )

    try:
        text_elements[ele_idx['refby']].text = 'Referred by:'
    except TypeError:
        with open('error_log.txt', 'a') as log:
            log.write('{}   Please verify that {} has no "Referred by:" field\n'.format(
                dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                phi_ecg)
            )

    for finding in range(ele_idx['finding_start'], ele_idx['finding_end']):
        try:
            finding_dt = parser.parse(text_elements[finding].text, fuzzy_with_tokens=True, ignoretz=True)
        except ValueError:
            continue

        deid_findingdt = finding_dt[0] - (ecg_date - deid_ecg_date)

        # super crude way of checking datetime format for now
        if text_elements[finding].text.count('-') == 2 and text_elements[finding].text.count(':') == 2:
            deid_findingdt = dt.datetime.strftime(deid_findingdt, strf_ecg).upper()
            phi_date = dt.datetime.strftime(finding_dt[0], strf_ecg)

        elif text_elements[finding].text.count('-') == 2 and text_elements[finding].text.count(':') == 1:
            deid_findingdt = dt.datetime.strftime(deid_findingdt, strf_ecg_alt).upper()
            phi_date = dt.datetime.strftime(finding_dt[0], strf_ecg_alt)

        elif text_elements[finding].text.count('-') == 2 and text_elements[finding].text.count(':') == 0:
            deid_findingdt = dt.datetime.strftime(deid_findingdt, '%d-%b-%Y').upper()
            phi_date = dt.datetime.strftime(finding_dt[0], '%d-%b-%Y')
            
        else:
            with open('error_log.txt', 'a') as log:
                log.write(
                    '{}  {} Unknown DT in finding: {}\n'.format(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                                         phi_ecg,
                                                                         text_elements[finding].text)
                )
                return

        idx = text_elements[finding].text.lower().find(phi_date.lower())  # get index pos of date in finding str

        text_elements[finding].text = ''.join([text_elements[finding].text[:idx],
                                               deid_findingdt,
                                               text_elements[finding].text[(idx + len(deid_findingdt)):]]
                                              )

    tree.write('{}/{}_{}_EKG.svg'.format(out_dir,
                                         list(id_key.get(mrn))[0],
                                         dt.datetime.strftime(ecg_key.get(mrn).get(ecg_date), '%Y-%m-%d')
                                         )
               )

    if os.path.exists('{}1.svg'.format(phi_svg.split('.')[0])):
        os.remove('{}1.svg'.format(phi_svg.split('.')[0]))
    else:
        with open('error_log.txt', 'a') as log:
            log.write("{}   Can't delete {}; file doesn't exist\n".format(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                                          phi_svg)
            )


def main(id_key_path, ecg_key_path, in_dir, out_dir):
    with open('error_log.txt', 'w') as log:
        log.write('{}   BEGIN LOGGING\n'.format(dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    id_key = defaultdict(dict)
    ecg_key = defaultdict(dict)

    with open(ecg_key_path, 'r') as f:
        next(f)
        read = csv.DictReader(f, fieldnames=['MRN', 'ECG_DATE', 'ECG_DATE_DEID'])
        for row in read:
            ecg_key[row['MRN']][dt.datetime.strptime(row['ECG_DATE'], '%Y-%m-%d %H:%M:%S')] \
                = dt.datetime.strptime(row['ECG_DATE_DEID'], '%Y-%m-%d %H:%M:%S')

    with open(id_key_path, 'r') as f:
        next(f)
        read = csv.DictReader(f, fieldnames=['MRN', 'PT_ID', 'BDAY_DEID'])
        for row in read:
            id_key[row['MRN']][row['PT_ID']] = dt.datetime.strptime(row['BDAY_DEID'], '%Y-%m-%d')

    if out_dir == '.':
        out_dir = 'Deidentified_ECGs'
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)

    for (dir, subdir, files) in tqdm(os.walk(in_dir)):
        for ecg in files:
            phi_ecg = os.path.join(dir, ecg)
            mrn = dir.split('\\')[-1]  # get MRN from folder name instead of the tracing
            deidentify(mrn, phi_ecg, ecg_key, id_key, out_dir)

    # for phi_ecg in tqdm([os.path.join(in_dir, x) for x in os.listdir(in_dir)]):
    #     deidentify(phi_ecg, ecg_key, id_key, out_dir)


if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='De-identify resting ECG recordings')
    argparser.add_argument('--input-dir', action='store', type=str, required=True, dest='in_dir',
                           help='Input directory of identified ECGs (SVG)')
    argparser.add_argument('--output-dir', action='store', type=str, required=True, dest='out_dir',
                           help='Output directory for de-identified ECGs')
    argparser.add_argument('--id-key', action='store', type=str, required=True, dest='id_key_path',
                           help='CSV with columns MRN, PT_ID, and de-identified birthday')
    argparser.add_argument('--ecg-key', action='store', type=str, required=True, dest='ecg_key_path',
                           help='CSV with columns MRN, TEST_DTTM, and de-identified TEST_DTTM')
    args = argparser.parse_args()

    main(id_key_path=args.id_key_path, ecg_key_path=args.ecg_key_path, in_dir=args.in_dir, out_dir=args.out_dir)
