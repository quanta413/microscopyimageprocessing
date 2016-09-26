# -*- coding: utf-8 -*-
"""
Spyder Editor

This is a temporary script file.
"""

import ipywidgets as ipyw
import matplotlib.pyplot as plt
import numpy as np
import skimage.transform as sktr
import skimage.restoration as skre
import skimage.draw as skdr
import skimage.feature as skfe
import skimage.filters as skf
import skimage.measure as skme
import skimage.morphology as skmo
import scipy.interpolate as spint
import scipy.ndimage as ndi
import scipy.ndimage.interpolation as ndint
import scipy.spatial.distance as spdist
import scipy.signal as spsig
from functools import partial


def inspectImages(image_lists, figsize):
    """
    This function is just a quick shortcut to making a slider for inspecting a
    group of related lists of images (such as TIRF and brightfield images of
    the same field of view). To make sense, the lists should contain fields of
    view in the same order from the same experiment.
    """
    sl_min = 0
    sl_max = len(image_lists[0])
    pic_num = len(image_lists)

    def displayImages(image_num):
        fig = plt.figure(figsize=figsize)
        plts = []
        for j in range(pic_num):
            plts.append(fig.add_subplot(1, pic_num, j+1))
            plts[j].imshow(image_lists[j][image_num])
    image_num = ipyw.IntSlider(value=sl_min, min=sl_min, max=sl_max,
                               continuous_update=False, description='image #')
    widget = ipyw.interactive(displayImages, image_num=image_num)
    return widget


def findBeadCenters(im):
    """
    This function finds the centers of 5 micron beads under
    100x magnification for TIRF or phase contrast pictures.
    """
    tr = skf.threshold_otsu(im)
    im_tr = im > tr
    im_new = skmo.remove_small_holes(im_tr, min_size=1000)
    im_new = skmo.remove_small_objects(im_new)
    im_edt = ndi.distance_transform_edt(im_new)
    centers = skfe.peak_local_max(im_edt, min_distance=10,
                                  exclude_border=5)
    return centers


def warpIm2Im(fr_im, to_im, aff_trans):
    """
    The affine transform is the one you get by calling the estimate
    method of the affine transform object of skimage.transform on
    points going from the to_im to the fr_im. In other words, it is
    the inverse of the matrix transformation that maps coordinates
    in the fr_im to the to_im which is what the function is doing.
    Don't ask why scipy.ndimage works that way it just does.
    """
    size = np.shape(to_im)
    sc_and_r = aff_trans.params[0:2, 0:2]
    trans = aff_trans.params[0:2, 2]
    warp = ndint.affine_transform(fr_im, sc_and_r, trans, size, order=0)
    return warp


def interpNans(im):
    """
    This function interpolates over any NANs in an image. It does
    this in place in memory, which is why it does not return a value.
    """
    mask = np.isnan(im)
    im[mask] = np.interp(np.flatnonzero(mask), np.flatnonzero(~mask),
                         im[~mask])


def normAndDenoisePc(im_col):
    """
    This corrects for unevenness in illumination by summing
    across a collection to get a blurry idea of the illumination
    and then dividing each image by that to correct for
    the uneven illumination. Then it denoises each image (meant
    for phase contrast images)
    """
    col_arr = np.array([image for image in im_col])
    ill_mean = np.mean(col_arr, 0)
    im_col_norm = [skre.denoise_nl_means(im/ill_mean,
                                         h=.95*np.std(im/ill_mean))
                   for im in im_col]
    [interpNans(image) for image in im_col_norm]
    # This last line before returning removes nans in the image caused
    # by denoise_nl_means or otherwise
    return im_col_norm


def threshPcHist(im, nbins=500, comp_width=20):
    """
    This function finds a cutoff between pixels for e coli
    in a histogram and pixels of background. It does this by
    assuming the background is taking up most of the field of view
    and taking the maximum of the histogram to represent the mean
    of the background (roughly). Then it finds the closest local
    minimum below this maximum and uses it as a cutoff. Meant for
    phase contrast images; will not work for brightfield or TIRF.
    """
    bins, edges = np.histogram(im, nbins)
    peak_ind = spsig.argrelextrema(bins, np.greater_equal,
                                   order=comp_width)[0]
    trof_ind = spsig.argrelextrema(bins, np.less_equal,
                                   order=comp_width)[0]
    peak_val = bins[peak_ind]
    bg_peak_ind = \
        peak_ind[np.where(peak_val == np.max(peak_val))][0]
    trof_left_bg = trof_ind[trof_ind < bg_peak_ind]
    try:
        low_thresh_ind = trof_left_bg[-1]
    except IndexError:
        raise IndexError('There was no minima left of \
            the global maximum')
    low_thresh_loc = edges[low_thresh_ind]
    return low_thresh_loc


def copyLabeledRegion(labeled_image, region_props, index):
    """
    This function makes a copy of a labeled region in an array and pads it with
    zeros.
    """
    y_min, x_min, y_max, x_max = region_props[index-1].bbox
    region = (labeled_image == index)
    new_copy = np.copy(region[y_min:y_max, x_min:x_max])
    new_copy = np.pad(new_copy, ((1, 1), (1, 1)), 'constant')
    return new_copy


def findContours(mask, level=.5):
    """
    This function just saves typing the level of contours I want
    in a binary mask (it's always .5). Also makes sure to save option
    that contours wind counterclockwise around islands of high pixels.
    """
    return skme.find_contours(mask, .5, positive_orientation='high')


def smoothSpline(contour, rel_s=10):
    """
    Returns a 3rd order b-spline approximation of a closed contour.
    It returns the points parametrized by curve length and then the
    coefficients and endpoints of each chunk of the spline.
    """
    xs = contour[:, 1]
    ys = contour[:, 0]
    diffx = np.diff(xs, 1)
    diffy = np.diff(ys, 1)
    difft = np.sqrt(diffx**2+diffy**2)**.5
    lengths = np.hstack((0, np.cumsum(difft)))
    n = lengths.size
    tck, u = spint.splprep(np.transpose(contour), u=lengths, per=1, s=n/rel_s)
    return u, tck


def splineContours(u, tck):
    """
    Returns points in contour of given spline evaluated at parameter values u.
    """
    return spint.splev(u, tck)


def curvature(u, tck):
    """
    Returns the curvature of the given spline evaluated at many points.
    """
    L = u[-1]
    t = np.linspace(0, L, 10*L)
    dx, dy = spint.splev(t, tck, der=1)
    ddx, ddy = spint.splev(t, tck, der=2)
    curvature = (dx*ddy-dy*ddx)/(dx**2+dy**2)**1.5
    return t, curvature


def minSignedCurvature(u, tck, curv=None, cutoff=-.2, ordr=10, ret='xy'):
    """
    This finds the points on a curve of maximum negative curvature (i.e. the
    minima of signed curvature)
    """
    if curv is None:
        curv = curvature(u, tck)
    temp_curv = np.copy(curv)
    temp_curv[temp_curv > cutoff] = 0
    peaks_index = spsig.argrelmin(temp_curv, order=ordr, mode='wrap')[0]
    peaks_u = u[peaks_index]
    try:
        peaks_xy = spint.splev(peaks_u, tck)
    except ValueError:
        peaks_xy = np.ndarray(0)
    if ret is 'xy':
        return peaks_xy
    elif ret is 'u':
        return peaks_u
    else:
        raise ValueError


def maxSignedCurvature(u, tck, curv=None, cutoff=.2, ordr=10, ret='xy'):
    if curv is None:
        curv = curvature(u, tck)
    temp_curv = np.copy(curv)
    temp_curv[temp_curv < cutoff] = 0
    peaks_index = spsig.argrelmax(temp_curv, order=ordr, mode='wrap')[0]
    peaks_u = u[peaks_index]
    try:
        peaks_xy = spint.splev(peaks_u, tck)
    except ValueError:
        peaks_xy = np.ndarray(0)
    if ret is 'xy':
        return peaks_xy
    elif ret is 'u':
        return peaks_u
    else:
        raise ValueError


def pointsonSpline(u, tck):
    """
    Returns points on a spline as an N by 2 array
    """
    x = spint.splev(u, tck, der=0)
    return np.transpose(np.array(x))


def antiNormalonSpline(u, tck):
    """
    Returns the negative of the normal vectors on a spline as an N by 2 array
    """
    x = spint.splev(u, tck, der=2)
    return np.transpose(np.array(x))


def normalizeArray(v):
    """
    Normalizes an array according to the frobenius norm.
    """
    return v / np.linalg.norm(v)


def angleBetween(v1, v2):
    """
    Returns the angle in radians between two vectors.
    """
    v1_unit = normalizeArray(v1)
    v2_unit = normalizeArray(v2)
    return np.arccos(np.clip(np.dot(v1_unit, v2_unit), -1.0, 1.0))


def parametricLine(t, point1, point2):
    """
    Defines a point on a line by two points on a line and a parametric
    parameter that is 0 at point 1 and 1 at point 2.
    """
    x1 = point1[0]
    y1 = point1[1]
    x2 = point2[0]
    y2 = point2[1]
    return np.array([(x2-x1)*t+x1, (y2-y1)*t+y1])


def connectingLine(p1, p2):
    """
    Returns the connecting line between two points given as 1d numpy arrays.
    """
    line = partial(parametricLine, point1=p1, point2=p2)
    return line


def vectorfromLine(line):
    """
    Returns a vector of the slope of a line
    """
    p1 = line.keywords['point1']
    x1 = p1[0]
    y1 = p1[1]
    p2 = line.keywords['point2']
    x2 = p2[0]
    y2 = p2[1]
    return np.array([x2-x1, y2-y1])


def extendLine(p1, v):
    """
    Returns a line extending from the given point along the given vector.
    """
    x2 = p1[0]+v[0]
    y2 = p1[1]+v[1]
    p2 = np.array([x2, y2])
    return connectingLine(p1, p2)


def lineIntersection(line1, line2):
    """
    Returns the intersection in (x,y) coordinates of two lines given as
    parametric functions.
    """
    p1_l1 = line1.keywords['point1']
    x1 = p1_l1[0]
    y1 = p1_l1[1]
    p2_l1 = line1.keywords['point2']
    x2 = p2_l1[0]
    y2 = p2_l1[1]
    p1_l2 = line2.keywords['point1']
    x3 = p1_l2[0]
    y3 = p1_l2[1]
    p2_l2 = line2.keywords['point2']
    x4 = p2_l2[0]
    y4 = p2_l2[1]
    slope1 = (y2-y1)/(x2-x1)
    slope2 = (y4-y3)/(x4-x3)
    if slope1 == slope2:
        raise ArithmeticError('''These lines either do not intersect or
                              intersect everywhere''')
    t_int = ((x4-x3)*(y1-y3)-(y4-y3)*(x1-x3))/((y4-y3)*(x2-x1)-(x4-x3)*(y2-y1))
    inter = line1(t_int)
    return inter


def effDist(r, d1, d2, theta1, theta2):
    if max(theta1, theta2) > np.pi/2:
        return np.inf
    elif max(r, d1, d2) > 100:
        return np.inf
    else:
        a = 1
        return a * ((2 - np.cos(theta1)-np.cos(theta2))*r)


def getInputforDist(p1, v1, p2, v2, thresh, dist_func):
    r = spdist.euclidean(p1, p2)
    cnction = connectingLine(p1, p2)
    cnction_vec = vectorfromLine(cnction)
    theta1 = angleBetween(v1, cnction_vec)
    theta2 = angleBetween(v2, -cnction_vec)
    ray1 = extendLine(p1, v1)
    ray2 = extendLine(p2, v2)
    inters = lineIntersection(ray1, ray2)
    d1 = spdist.euclidean(p1, inters)
    d2 = spdist.euclidean(p2, inters)
    d_prime = dist_func(r, d1, d2, theta1, theta2)
    return d_prime


def makeGeom(p1, v1, p2, v2):
    ray1 = extendLine(p1, v1)
    ray2 = extendLine(p2, v2)
    inters = lineIntersection(ray1, ray2)
    l1 = skdr.line(p1[0].astype('int'), p1[1].astype('int'),
                   inters[0].astype('int'), inters[1].astype('int'))
    l2 = skdr.line(p2[0].astype('int'), p2[1].astype('int'),
                   inters[0].astype('int'), inters[1].astype('int'))
    return l1, l2


def pvPairGenerator(points, vectors):
    imax = points.shape[0]
    i = 1
    while i < imax:
        j = 0
        while j < i:
            yield points[j], vectors[j], points[i], vectors[i]
            j += 1
        i += 1


def polar2cart(r, theta, center):
    """
    Got this function from a stackexchange answer about mapping image from
    cartesian to polar
    """
    x = r * np.cos(theta) + center[0]
    y = r * np.sin(theta) + center[1]
    return x, y


def img2polar(img, center, final_radius, initial_radius=None,
              phase_width=3000):
    """
    Got this function from a stackexchange answer about mapping image from
    cartesian to polar
    """

    if initial_radius is None:
        initial_radius = 0

    theta, R = np.meshgrid(np.linspace(0, 2*np.pi, phase_width),
                           np.arange(initial_radius, final_radius))

    Xcart, Ycart = polar2cart(R, theta, center)

    Xcart = Xcart.astype(int)
    Ycart = Ycart.astype(int)

    if img.ndim == 3:
        polar_img = img[Ycart, Xcart, :]
        polar_img = np.reshape(polar_img, (final_radius-initial_radius,
                                           phase_width, 3))
    else:
        polar_img = img[Ycart, Xcart]
        polar_img = np.reshape(polar_img, (final_radius-initial_radius,
                                           phase_width))

    return polar_img
